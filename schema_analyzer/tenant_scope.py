"""Per-entity tenant-scope classification for multi-tenant graphs.

Closes issue #13 — emit ``tenantScope`` annotations on physical-mapping
entities so every downstream consumer (transpilers, NL→Cypher
pipelines, agentic clients, dashboards) shares one canonical
classification of which collections are tenant-scoped, which require
graph traversal, and which are global metadata.

Three roles are recognised:

* ``tenant_root`` — the entity that anchors the tenant hierarchy
  (typically named ``Tenant``). At most one per mapping.
* ``tenant_scoped`` — belongs to a single tenant. Either carries a
  denormalised tenant-reference field (``tenantField``) on every
  document, or can only be reached via traversal from the tenant
  root.
* ``global`` — intentionally cross-tenant reference data
  (e.g. ``Cve``, ``AppVersion``, …). Querying these collections
  must NOT include any tenant filter.

Detection is deterministic and depends only on the conceptual schema
+ physical mapping (no LLM call). It runs after reconciliation so
backfilled entities are also classified, and before validation so
the response schema check covers the new field.

When the input ``data`` already carries an explicit ``tenantScope``
annotation on an entity (e.g. supplied by an operator override or a
future LLM extension), it wins outright — the annotator only fills
in missing entries. This mirrors the precedence contract used by
:mod:`schema_analyzer.reconcile` for entities and lets operators
override edge cases (a vestigial ``TENANT_ID`` column on a
collection that is in fact intentionally global).
"""

from __future__ import annotations

import logging
import os
import re
from collections import deque
from typing import Any, Literal

from .defaults import (
    TENANT_SCOPE_FIELD_REGEX,
    TENANT_SCOPE_MAX_HOPS,
    TENANT_SCOPE_ROOT_NAMES,
)
from .utils import entity_property_names

logger = logging.getLogger(__name__)

# --- Defaults ---------------------------------------------------------------

# Defaults are sourced from :mod:`schema_analyzer.defaults` so operators
# can change them in one place. The values exported below are the
# *resolved* forms (compiled regex; bare ints) used by the annotator
# and by tests/consumers that want to mirror the same defaults.

_DEFAULT_TENANT_FIELD_REGEX = re.compile(TENANT_SCOPE_FIELD_REGEX, re.IGNORECASE)

DEFAULT_TENANT_ROOT_NAMES: tuple[str, ...] = TENANT_SCOPE_ROOT_NAMES

DEFAULT_MAX_HOPS: int = TENANT_SCOPE_MAX_HOPS

# Discovery sources surfaced in the tenantScopeReport metadata block.
TenantScopeRole = Literal["tenant_root", "tenant_scoped", "global"]
TenantScopeSource = Literal[
    "explicit_annotation",
    "denorm_field_heuristic",
    "traversal_reachability",
    "tenant_root",
    "global_default",
]

# --- Configuration ----------------------------------------------------------


def _resolve_tenant_field_regex(
    explicit: re.Pattern[str] | None,
) -> re.Pattern[str]:
    if explicit is not None:
        return explicit
    raw = os.environ.get("SCHEMA_ANALYZER_TENANT_FIELD_REGEX")
    if not raw:
        return _DEFAULT_TENANT_FIELD_REGEX
    try:
        return re.compile(raw, re.IGNORECASE)
    except re.error:
        logger.warning(
            "Invalid SCHEMA_ANALYZER_TENANT_FIELD_REGEX %r; falling back to default",
            raw,
        )
        return _DEFAULT_TENANT_FIELD_REGEX


def _resolve_tenant_root_names(
    explicit: tuple[str, ...] | None,
) -> tuple[str, ...]:
    if explicit:
        return explicit
    raw = os.environ.get("SCHEMA_ANALYZER_TENANT_ROOT_NAMES")
    if not raw:
        return DEFAULT_TENANT_ROOT_NAMES
    parts = tuple(p.strip() for p in raw.split(",") if p.strip())
    return parts or DEFAULT_TENANT_ROOT_NAMES


def _resolve_max_hops(explicit: int | None) -> int:
    if explicit is not None:
        return max(0, int(explicit))
    raw = os.environ.get("SCHEMA_ANALYZER_TENANT_SCOPE_MAX_HOPS")
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            logger.warning(
                "Invalid SCHEMA_ANALYZER_TENANT_SCOPE_MAX_HOPS %r; falling back to default",
                raw,
            )
    return DEFAULT_MAX_HOPS


# --- Helpers ----------------------------------------------------------------


def _endpoint_label(endpoint: Any) -> str | None:
    if isinstance(endpoint, str):
        return endpoint or None
    if isinstance(endpoint, dict):
        for key in ("label", "name", "entity", "type"):
            v = endpoint.get(key)
            if isinstance(v, str) and v:
                return v
    return None


def _build_relationship_graph(cs: dict[str, Any]) -> dict[str, set[str]]:
    """Undirected adjacency across the conceptual relationships.

    Tolerates both flat-string and dict endpoint shapes, and both the
    historical ``sourceEntity`` / ``targetEntity`` keys and the newer
    ``from`` / ``to`` keys.
    """
    rels = cs.get("relationships") or []
    adj: dict[str, set[str]] = {}
    if not isinstance(rels, list):
        return adj
    for r in rels:
        if not isinstance(r, dict):
            continue
        src = _endpoint_label(
            r.get("from") or r.get("source") or r.get("sourceEntity"),
        )
        dst = _endpoint_label(
            r.get("to") or r.get("target") or r.get("targetEntity"),
        )
        if not src or not dst:
            continue
        adj.setdefault(src, set()).add(dst)
        adj.setdefault(dst, set()).add(src)
    return adj


def _reachable_from(
    start: str,
    adj: dict[str, set[str]],
    *,
    max_hops: int,
) -> set[str]:
    seen: set[str] = {start}
    if max_hops <= 0:
        return seen
    frontier: deque[tuple[str, int]] = deque([(start, 0)])
    while frontier:
        node, depth = frontier.popleft()
        if depth >= max_hops:
            continue
        for neighbour in adj.get(node, ()):
            if neighbour not in seen:
                seen.add(neighbour)
                frontier.append((neighbour, depth + 1))
    return seen


def _find_denorm_field(
    entity: dict[str, Any],
    regex: re.Pattern[str],
) -> str | None:
    for prop_name in entity_property_names(entity):
        if regex.match(prop_name):
            return prop_name
    return None


def _explicit_scope_from_entry(
    entry: dict[str, Any] | None,
) -> tuple[TenantScopeRole | None, str | None]:
    """Return ``(role, tenantField)`` for an explicit override on
    ``physicalMapping.entities[name]`` or ``(None, None)`` when no
    annotation is present / it is malformed."""
    if not isinstance(entry, dict):
        return None, None
    ts = entry.get("tenantScope")
    if not isinstance(ts, dict):
        return None, None
    raw_role = ts.get("role")
    if raw_role not in ("tenant_root", "tenant_scoped", "global"):
        return None, None
    role: TenantScopeRole = raw_role  # type: ignore[assignment]
    if role != "tenant_scoped":
        return role, None
    field_name = ts.get("tenantField")
    if not isinstance(field_name, str) or not field_name:
        field_name = None
    return role, field_name


# --- Public entry point -----------------------------------------------------


def annotate_tenant_scope(
    data: dict[str, Any],
    *,
    tenant_root_names: tuple[str, ...] | None = None,
    tenant_field_regex: re.Pattern[str] | None = None,
    max_hops: int | None = None,
) -> dict[str, Any] | None:
    """Annotate ``physicalMapping.entities[*].tenantScope`` in place.

    Returns a summary dict suitable for
    ``metadata.tenantScopeReport``, or ``None`` when no tenant root
    was detected (in which case nothing is mutated and no metadata
    block should be emitted — symmetric to
    :func:`schema_analyzer.reconcile.reconcile_physical_mapping`).

    Parameters
    ----------
    data:
        The analyzer's raw output dict (post-reconciliation,
        pre-validation), with ``conceptualSchema`` and
        ``physicalMapping`` keys at the top level.
    tenant_root_names:
        Override the set of conceptual entity names that count as
        tenant roots. Defaults to env var
        ``SCHEMA_ANALYZER_TENANT_ROOT_NAMES`` then ``("Tenant",)``.
    tenant_field_regex:
        Override the regex used to detect denormalised tenant-id
        fields. Defaults to env var
        ``SCHEMA_ANALYZER_TENANT_FIELD_REGEX`` then
        ``^tenant[_-]?(id|key)$``.
    max_hops:
        BFS depth cap when deciding whether a non-denorm entity is
        reachable from the tenant root. Defaults to env var
        ``SCHEMA_ANALYZER_TENANT_SCOPE_MAX_HOPS`` then ``5``.
    """
    cs = data.get("conceptualSchema")
    pm = data.get("physicalMapping")
    if not isinstance(cs, dict) or not isinstance(pm, dict):
        return None
    pm_entities = pm.get("entities")
    if not isinstance(pm_entities, dict):
        return None

    entities = cs.get("entities")
    if not isinstance(entities, list):
        return None

    by_name: dict[str, dict[str, Any]] = {}
    for e in entities:
        if isinstance(e, dict):
            name = e.get("name")
            if isinstance(name, str) and name:
                by_name[name] = e

    root_names = _resolve_tenant_root_names(tenant_root_names)
    tenant_entity: str | None = next(
        (n for n in root_names if n in by_name),
        None,
    )
    if tenant_entity is None:
        # Single-tenant graph — leave the mapping byte-identical so
        # this annotator is a no-op for callers that don't have a
        # Tenant collection. They get the same export they got before
        # the feature landed.
        return None

    field_regex = _resolve_tenant_field_regex(tenant_field_regex)
    hops = _resolve_max_hops(max_hops)

    adj = _build_relationship_graph(cs)
    reachable = _reachable_from(tenant_entity, adj, max_hops=hops)

    counts = {
        "explicit_annotation": 0,
        "denorm_field_heuristic": 0,
        "traversal_reachability": 0,
        "tenant_root": 0,
        "global_default": 0,
    }
    role_counts = {"tenant_root": 0, "tenant_scoped": 0, "global": 0}
    denorm_with_field = 0
    traversal_only = 0

    for name, entity in by_name.items():
        entry = pm_entities.get(name)
        if not isinstance(entry, dict):
            # Conceptual entity with no physical mapping — nothing to
            # annotate. The reconciliation step is supposed to backfill
            # these; warn quietly and skip rather than crash.
            continue

        explicit_role, explicit_field = _explicit_scope_from_entry(entry)
        if explicit_role is not None:
            _set_scope(
                entry,
                explicit_role,
                explicit_field,
                tenant_entity,
            )
            counts["explicit_annotation"] += 1
            role_counts[explicit_role] += 1
            if explicit_role == "tenant_scoped" and explicit_field:
                denorm_with_field += 1
            elif explicit_role == "tenant_scoped":
                traversal_only += 1
            continue

        if name == tenant_entity:
            _set_scope(entry, "tenant_root", None, tenant_entity)
            counts["tenant_root"] += 1
            role_counts["tenant_root"] += 1
            continue

        denorm = _find_denorm_field(entity, field_regex)
        if denorm is not None:
            _set_scope(entry, "tenant_scoped", denorm, tenant_entity)
            counts["denorm_field_heuristic"] += 1
            role_counts["tenant_scoped"] += 1
            denorm_with_field += 1
            continue

        if name in reachable:
            _set_scope(entry, "tenant_scoped", None, tenant_entity)
            counts["traversal_reachability"] += 1
            role_counts["tenant_scoped"] += 1
            traversal_only += 1
            continue

        _set_scope(entry, "global", None, tenant_entity)
        counts["global_default"] += 1
        role_counts["global"] += 1

    return {
        "tenantEntity": tenant_entity,
        "denormScopedCount": denorm_with_field,
        "traversalScopedCount": traversal_only,
        "globalCount": role_counts["global"],
        "tenantFieldRegex": field_regex.pattern,
        "discovery": {
            "fromExplicitAnnotation": counts["explicit_annotation"],
            "fromDenormFieldHeuristic": counts["denorm_field_heuristic"],
            "fromTraversalReachability": counts["traversal_reachability"],
        },
    }


def _set_scope(
    entry: dict[str, Any],
    role: TenantScopeRole,
    tenant_field: str | None,
    tenant_entity: str,
) -> None:
    """Write the ``tenantScope`` block onto a physical-mapping entry.

    The ``global`` role omits ``tenantEntity`` since the entity is
    explicitly NOT tenant-bound. The ``tenant_root`` role omits both
    ``tenantField`` and ``tenantEntity`` (the root IS the tenant
    entity).
    """
    block: dict[str, Any] = {"role": role}
    if role == "tenant_scoped":
        block["tenantEntity"] = tenant_entity
        if tenant_field:
            block["tenantField"] = tenant_field
    entry["tenantScope"] = block


__all__ = [
    "DEFAULT_MAX_HOPS",
    "DEFAULT_TENANT_ROOT_NAMES",
    "TenantScopeRole",
    "TenantScopeSource",
    "annotate_tenant_scope",
]
