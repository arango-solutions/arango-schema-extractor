"""
Post-LLM reconciliation: ensure every non-system collection in the physical
schema snapshot is represented in the exported mapping.

Issue #5. The LLM path is free to omit collections (token pressure,
salience bias, etc.) and historically every downstream consumer had to
re-implement a completeness backfill. This module runs that backfill once,
as a property of the analyzer itself, using deterministic baseline
inference.

Exported surface:

* :func:`reconcile_physical_mapping` — in-place merge missing collections
  from the baseline into the LLM's ``data`` dict, returning a summary
  suitable for ``metadata.reconciliation`` (or ``None`` when no backfill
  was needed).
* :func:`collections_referenced_by_mapping` — helper exposed for tests and
  downstream diagnostics.
"""

from __future__ import annotations

from typing import Any

from .baseline import infer_baseline_from_snapshot

RECONCILIATION_STRATEGY = "baseline_per_missing_collection"


def snapshot_collection_names(snapshot: dict[str, Any]) -> set[str]:
    """
    Return the set of physical collection names present in ``snapshot``.

    Used by :func:`strip_unknown_collection_names` to allowlist
    LLM-supplied ``collectionName`` / ``edgeCollectionName`` values
    before they are persisted in the analysis output.
    """
    out: set[str] = set()
    for c in snapshot.get("collections") or []:
        if isinstance(c, dict):
            name = c.get("name")
            if isinstance(name, str) and name:
                out.add(name)
    return out


def strip_unknown_collection_names(
    data: dict[str, Any],
    snapshot: dict[str, Any],
) -> list[str]:
    """
    Remove any ``collectionName`` / ``edgeCollectionName`` value supplied
    by the LLM that does not correspond to a real collection in
    ``snapshot``. Returns the list of warning strings to append to
    ``metadata.warnings`` (one per stripped reference).

    This guards against LLM hallucinations leaking into the
    ``physicalMapping`` and being treated as authoritative by downstream
    consumers (transpilers, NL→Cypher prompt builders, etc.). Stripped
    entries become eligible for backfill by
    :func:`reconcile_physical_mapping`.
    """
    allowed = snapshot_collection_names(snapshot)
    if not allowed:
        return []
    pm = data.get("physicalMapping")
    if not isinstance(pm, dict):
        return []
    warnings: list[str] = []

    entities = pm.get("entities")
    if isinstance(entities, dict):
        for ent_name, entry in entities.items():
            if not isinstance(entry, dict):
                continue
            col = entry.get("collectionName")
            if isinstance(col, str) and col and col not in allowed:
                warnings.append(
                    f"Stripped LLM-hallucinated collectionName {col!r} from entity {ent_name!r}"
                )
                entry.pop("collectionName", None)

    relationships = pm.get("relationships")
    if isinstance(relationships, dict):
        for rel_name, entry in relationships.items():
            if not isinstance(entry, dict):
                continue
            for field in ("edgeCollectionName", "collectionName"):
                col = entry.get(field)
                if isinstance(col, str) and col and col not in allowed:
                    warnings.append(
                        f"Stripped LLM-hallucinated {field} {col!r} from relationship {rel_name!r}"
                    )
                    entry.pop(field, None)

    return warnings


def collections_referenced_by_mapping(physical_mapping: dict[str, Any]) -> set[str]:
    """
    Return the set of physical collection names that are referenced by at
    least one entity or relationship entry in ``physical_mapping``.

    Relationships may legitimately carry either ``edgeCollectionName``
    (preferred; the only form after issue #6 lands) or the pre-#6
    ``collectionName`` — both are recognised so this helper is stable
    across the rename.
    """
    names: set[str] = set()
    if not isinstance(physical_mapping, dict):
        return names

    entities = physical_mapping.get("entities")
    if isinstance(entities, dict):
        for entry in entities.values():
            if not isinstance(entry, dict):
                continue
            col = entry.get("collectionName")
            if isinstance(col, str) and col:
                names.add(col)

    relationships = physical_mapping.get("relationships")
    if isinstance(relationships, dict):
        for entry in relationships.values():
            if not isinstance(entry, dict):
                continue
            col = entry.get("edgeCollectionName") or entry.get("collectionName")
            if isinstance(col, str) and col:
                names.add(col)

    return names


def reconcile_physical_mapping(
    data: dict[str, Any],
    snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    """
    In-place backfill of collections that are present in the snapshot but
    absent from the LLM's physical mapping.

    Mutates ``data`` (the LLM's raw output dict) to add any missing entity
    or relationship entries, using the deterministic baseline's
    classification (``COLLECTION`` / ``LABEL`` / ``DEDICATED_COLLECTION`` /
    ``GENERIC_WITH_TYPE``). Conceptual-schema counterparts for the
    backfilled mappings are merged alongside.

    Returns a reconciliation summary dict when backfilling occurred, or
    ``None`` when the LLM output already covered every snapshot collection
    (so callers can omit the ``metadata.reconciliation`` key entirely —
    the contract says do not emit an empty key).
    """
    snapshot_col_names = [
        c.get("name")
        for c in (snapshot.get("collections") or [])
        if isinstance(c, dict) and isinstance(c.get("name"), str) and c.get("name")
    ]
    if not snapshot_col_names:
        return None

    pm = data.get("physicalMapping")
    if not isinstance(pm, dict):
        pm = {}
        data["physicalMapping"] = pm

    covered = collections_referenced_by_mapping(pm)
    missing = [n for n in snapshot_col_names if n not in covered]
    if not missing:
        return None

    baseline = infer_baseline_from_snapshot(snapshot)
    baseline_pm = baseline.get("physicalMapping") or {}
    baseline_cs = baseline.get("conceptualSchema") or {}

    pm_entities = pm.setdefault("entities", {})
    pm_rels = pm.setdefault("relationships", {})
    cs = data.setdefault("conceptualSchema", {})
    if not isinstance(cs, dict):
        cs = {}
        data["conceptualSchema"] = cs
    cs_entities = cs.setdefault("entities", [])
    cs_rels = cs.setdefault("relationships", [])

    existing_entity_names: set[str] = set(pm_entities.keys())
    existing_rel_types: set[str] = set(pm_rels.keys())
    existing_cs_entity_names: set[str] = {
        e.get("name") for e in cs_entities if isinstance(e, dict) and isinstance(e.get("name"), str)
    }
    existing_cs_rel_types: set[str] = {
        r.get("type") for r in cs_rels if isinstance(r, dict) and isinstance(r.get("type"), str)
    }

    missing_set = set(missing)
    backfilled: set[str] = set()

    for name, entry in (baseline_pm.get("entities") or {}).items():
        if not isinstance(entry, dict):
            continue
        col = entry.get("collectionName")
        if not isinstance(col, str) or col not in missing_set:
            continue
        if name not in existing_entity_names:
            pm_entities[name] = entry
            existing_entity_names.add(name)
        if name not in existing_cs_entity_names:
            for bc in baseline_cs.get("entities") or []:
                if isinstance(bc, dict) and bc.get("name") == name:
                    cs_entities.append(bc)
                    existing_cs_entity_names.add(name)
                    break
        backfilled.add(col)

    for rtype, entry in (baseline_pm.get("relationships") or {}).items():
        if not isinstance(entry, dict):
            continue
        col = entry.get("edgeCollectionName") or entry.get("collectionName")
        if not isinstance(col, str) or col not in missing_set:
            continue
        if rtype not in existing_rel_types:
            pm_rels[rtype] = entry
            existing_rel_types.add(rtype)
        if rtype not in existing_cs_rel_types:
            for br in baseline_cs.get("relationships") or []:
                if isinstance(br, dict) and br.get("type") == rtype:
                    cs_rels.append(br)
                    existing_cs_rel_types.add(rtype)
                    break
        backfilled.add(col)

    if not backfilled:
        return None

    return {
        "llm_covered_collections": len(covered & set(snapshot_col_names)),
        "snapshot_collections": len(snapshot_col_names),
        "backfilled_collections": sorted(backfilled),
        "strategy": RECONCILIATION_STRATEGY,
    }
