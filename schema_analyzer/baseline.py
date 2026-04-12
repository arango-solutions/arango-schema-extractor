from __future__ import annotations

from typing import Any

from .conceptual import ConceptualSchema
from .mapping import PhysicalMapping
from .utils import pascal_case

PREFERRED_EDGE_TYPE_FIELDS: list[str] = ["relation", "relType", "type"]
PREFERRED_DOC_TYPE_FIELDS: list[str] = ["type", "_type", "kind", "entityType", "label"]


def _choose_type_field(col: dict[str, Any], *, is_edge: bool) -> str | None:
    """
    Pick the best type field from snapshot stats (deterministic).
    """
    candidates = col.get("candidate_type_fields") or []
    if not isinstance(candidates, list):
        candidates = []
    value_counts = col.get("sample_field_value_counts") or {}
    if not isinstance(value_counts, dict):
        value_counts = {}

    preferred = PREFERRED_EDGE_TYPE_FIELDS if is_edge else PREFERRED_DOC_TYPE_FIELDS
    ordered = [c for c in preferred if c in candidates] + [c for c in candidates if c not in preferred]

    def distinct_count(field: str) -> int:
        items = value_counts.get(field)
        if not isinstance(items, list):
            return 0
        # count distinct values with at least 1 occurrence
        seen = set()
        for it in items:
            if isinstance(it, dict) and "value" in it:
                seen.add(str(it["value"]))
        return len(seen)

    best = None
    best_n = 0
    for f in ordered:
        n = distinct_count(f)
        if n > best_n:
            best = f
            best_n = n
    if best_n >= 2:
        return best
    return None


def _iter_type_values(col: dict[str, Any], field: str) -> list[str]:
    value_counts = col.get("sample_field_value_counts") or {}
    if not isinstance(value_counts, dict):
        return []
    items = value_counts.get(field)
    if not isinstance(items, list):
        return []
    values = []
    for it in items:
        if isinstance(it, dict) and "value" in it:
            v = str(it["value"]).strip()
            if v:
                values.append(v)
    # deterministic ordering
    return sorted(set(values))


def infer_baseline_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """
    Deterministic baseline inference from a physical schema snapshot.

    Guarantees non-empty, structurally valid analysis output even when no LLM is available.
    """
    cs = ConceptualSchema.empty()
    pm = PhysicalMapping.empty()

    collections = snapshot.get("collections") or []
    if not isinstance(collections, list):
        collections = []

    # Entities
    for col in collections:
        if not isinstance(col, dict):
            continue
        if col.get("type") != "document":
            continue

        collection_name = col.get("name")
        if not isinstance(collection_name, str) or not collection_name:
            continue

        type_field = _choose_type_field(col, is_edge=False)
        if type_field:
            for raw in _iter_type_values(col, type_field):
                ent_name = pascal_case(raw)
                cs.entities.append({"name": ent_name, "labels": [ent_name], "properties": []})
                pm.entities[ent_name] = {
                    "style": "LABEL",
                    "collectionName": collection_name,
                    "typeField": type_field,
                    "typeValue": raw,
                }
        else:
            ent_name = col.get("inferred_entity_type") or pascal_case(collection_name)
            cs.entities.append({"name": ent_name, "labels": [ent_name], "properties": []})
            pm.entities[ent_name] = {"style": "COLLECTION", "collectionName": collection_name}

    # Relationships
    for col in collections:
        if not isinstance(col, dict):
            continue
        if col.get("type") != "edge":
            continue

        collection_name = col.get("name")
        if not isinstance(collection_name, str) or not collection_name:
            continue

        type_field = _choose_type_field(col, is_edge=True)
        if type_field:
            for raw in _iter_type_values(col, type_field):
                rel_type = str(raw).strip()
                if not rel_type:
                    continue
                cs.relationships.append(
                    {
                        "type": rel_type,
                        # Best-effort: endpoints are unknown without data inspection.
                        "fromEntity": "Any",
                        "toEntity": "Any",
                        "properties": [],
                    }
                )
                pm.relationships[rel_type] = {
                    "style": "GENERIC_WITH_TYPE",
                    "collectionName": collection_name,
                    "typeField": type_field,
                    "typeValue": raw,
                }
        else:
            rel_type = col.get("inferred_relationship_type") or collection_name.upper()
            cs.relationships.append({"type": rel_type, "fromEntity": "Any", "toEntity": "Any", "properties": []})
            pm.relationships[rel_type] = {"style": "DEDICATED_COLLECTION", "edgeCollectionName": collection_name}

    return {"conceptualSchema": cs.to_json(), "physicalMapping": pm.to_json()}
