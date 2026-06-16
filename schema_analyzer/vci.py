"""Vertex-Centric Index (VCI) detection (PRD §6.1 / §6.2).

ArangoDB query planners can serve a traversal directly from a persistent index
rooted at ``_from`` / ``_to`` when that index also carries the discriminator (and
sometimes denormalized) fields a filter touches — the classic *vertex-centric
index* optimization. Surfacing this lets downstream transpilers/planners emit a
VCI-aware traversal instead of a generic edge scan + filter.

Two complementary, deterministic signals are computed straight from the
snapshot (no extra DB round-trip):

* **Index-level** — persistent indexes on an edge collection whose first field
  is ``_from`` or ``_to`` plus one or more trailing discriminator fields
  (e.g. ``[_from, type]``, ``[_to, type, validFrom]``). Reports the
  participating discriminator fields, ``unique`` / ``sparse``, and the access
  pattern (``out-edge`` / ``in-edge`` / ``both``).
* **Denormalization** — edge attributes that duplicate properties of their
  endpoint vertex collections (denormalized onto the edge for VCI lookups).
  Reports the duplicated field and its source vertex collection(s).

When either signal fires for a relationship, the relationship's physical mapping
entry gains a ``vci`` block and ``vciCandidate: true`` *alongside* its existing
``DEDICATED_COLLECTION`` / ``GENERIC_WITH_TYPE`` style (the style is never
overwritten). A ``metadata.vci`` summary lists the relationships involved.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_SYSTEM_FIELDS = frozenset({"_key", "_id", "_rev", "_from", "_to"})
_EDGE_ROOTS = ("_from", "_to")


def _observed_field_names(entry: dict[str, Any]) -> set[str]:
    """Union of field names observed on a collection entry (PG ``fields`` or
    LPG ``by_type`` groupings)."""
    observed = entry.get("observed_fields")
    names: set[str] = set()
    if not isinstance(observed, dict):
        return names
    fields = observed.get("fields")
    if isinstance(fields, list):
        names.update(f for f in fields if isinstance(f, str))
    by_type = observed.get("by_type")
    if isinstance(by_type, dict):
        for field_list in by_type.values():
            if isinstance(field_list, list):
                names.update(f for f in field_list if isinstance(f, str))
    return names


def _collections_by_name(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for c in snapshot.get("collections", []) or []:
        if isinstance(c, dict) and isinstance(c.get("name"), str):
            out[c["name"]] = c
    return out


def _index_level_vci(collection: dict[str, Any]) -> list[dict[str, Any]]:
    """Persistent indexes rooted at _from/_to with trailing discriminator fields."""
    found: list[dict[str, Any]] = []
    for idx in collection.get("indexes", []) or []:
        if not isinstance(idx, dict) or idx.get("type") != "persistent":
            continue
        fields = idx.get("fields")
        if not isinstance(fields, list) or len(fields) < 2:
            continue
        root = fields[0]
        if root not in _EDGE_ROOTS:
            continue
        discriminators = [f for f in fields[1:] if isinstance(f, str)]
        if not discriminators:
            continue
        found.append(
            {
                "root": root,
                "fields": [f for f in fields if isinstance(f, str)],
                "discriminatorFields": discriminators,
                "unique": bool(idx.get("unique", False)),
                "sparse": bool(idx.get("sparse", False)),
            }
        )
    return found


def _access_pattern(indexes: list[dict[str, Any]]) -> str:
    roots = {i["root"] for i in indexes}
    if "_from" in roots and "_to" in roots:
        return "both"
    if "_from" in roots:
        return "out-edge"
    return "in-edge"


def _denormalized_fields(
    edge_entry: dict[str, Any],
    endpoints: dict[str, Any],
    collections: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Edge fields that also appear on an endpoint vertex collection."""
    edge_fields = _observed_field_names(edge_entry) - _SYSTEM_FIELDS
    if not edge_fields:
        return []
    endpoint_names: list[str] = []
    for key in ("from_collections", "to_collections"):
        vals = endpoints.get(key)
        if isinstance(vals, list):
            endpoint_names.extend(v for v in vals if isinstance(v, str))

    by_field: dict[str, set[str]] = {}
    for cname in endpoint_names:
        vertex = collections.get(cname)
        if not isinstance(vertex, dict):
            continue
        shared = (_observed_field_names(vertex) - _SYSTEM_FIELDS) & edge_fields
        for f in shared:
            by_field.setdefault(f, set()).add(cname)

    return [{"field": f, "sourceCollections": sorted(srcs)} for f, srcs in sorted(by_field.items())]


def detect_vci(data: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Annotate relationship mappings with VCI signals and return a summary.

    Returns ``None`` when there is nothing to classify (no relationship
    mappings). Otherwise returns ``{"relationships": [..names..], "status":
    "ok"}`` and mutates ``data["physicalMapping"]["relationships"][rt]`` in
    place, adding a ``vci`` block + ``vciCandidate: true`` to each match.
    """
    pm = data.get("physicalMapping")
    if not isinstance(pm, dict):
        return None
    rels = pm.get("relationships")
    if not isinstance(rels, dict) or not rels:
        return None

    collections = _collections_by_name(snapshot)
    matched: list[str] = []

    for rtype, mapping in rels.items():
        if not isinstance(mapping, dict):
            continue
        edge_name = mapping.get("edgeCollectionName")
        if not isinstance(edge_name, str) or edge_name not in collections:
            continue
        edge_entry = collections[edge_name]

        index_vci = _index_level_vci(edge_entry)
        raw_endpoints = edge_entry.get("edge_endpoints")
        endpoints = raw_endpoints if isinstance(raw_endpoints, dict) else {}
        denorm = _denormalized_fields(edge_entry, endpoints, collections)

        if not index_vci and not denorm:
            continue

        vci_block: dict[str, Any] = {}
        if index_vci:
            vci_block["indexLevel"] = {
                "accessPattern": _access_pattern(index_vci),
                "indexes": index_vci,
            }
        if denorm:
            vci_block["denormalization"] = {"duplicatedFields": denorm}

        mapping["vci"] = vci_block
        mapping["vciCandidate"] = True
        matched.append(rtype)

    if not matched:
        return None

    return {"status": "ok", "relationships": sorted(matched)}
