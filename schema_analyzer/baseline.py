from __future__ import annotations

from typing import Any

from .conceptual import ConceptualSchema
from .defaults import MIN_TYPE_FIELD_DISTINCT_VALUES, UNRESOLVED_ENDPOINT
from .mapping import PhysicalMapping
from .snapshot import (
    _pick_best_type_field,
    _type_values_for_field,
    infer_entity_type_from_collection_name,
)
from .utils import pascal_case


def _choose_type_field(col: dict[str, Any], *, is_edge: bool) -> str | None:
    return _pick_best_type_field(col, is_edge=is_edge)


def _iter_type_values(col: dict[str, Any], field: str) -> list[str]:
    return _type_values_for_field(col, field)


def _resolve_endpoints(
    col: dict[str, Any],
    rel_type: str,
    *,
    is_generic: bool,
    collection_to_entities: dict[str, list[str]],
) -> tuple[str, str]:
    """
    Resolve fromEntity / toEntity for a relationship type.

    Resolution chain for generic edges:
    1. entity_types_by_relation (LPG — per-relation entity type via DOCUMENT())
    2. collections_by_relation (hybrid — per-relation from/to collections)
    3. from_collections / to_collections (union across all relation types)
    4. UNRESOLVED_ENDPOINT ("Any")
    """
    endpoints = col.get("edge_endpoints") or {}

    if is_generic:
        # Strategy 1: LPG entity type resolution
        by_rel = endpoints.get("entity_types_by_relation") or {}
        rel_ep = by_rel.get(rel_type)
        if rel_ep:
            from_types = rel_ep.get("from_entity_types") or []
            to_types = rel_ep.get("to_entity_types") or []
            from_ent = pascal_case(from_types[0]) if len(from_types) == 1 else UNRESOLVED_ENDPOINT
            to_ent = pascal_case(to_types[0]) if len(to_types) == 1 else UNRESOLVED_ENDPOINT
            return from_ent, to_ent

        # Strategy 2: Hybrid per-relation collection resolution
        cols_by_rel = endpoints.get("collections_by_relation") or {}
        rel_cols = cols_by_rel.get(rel_type)
        if rel_cols:
            from_cols = rel_cols.get("from_collections") or []
            to_cols = rel_cols.get("to_collections") or []
            from_ent = _resolve_single_entity(from_cols, collection_to_entities)
            to_ent = _resolve_single_entity(to_cols, collection_to_entities)
            return from_ent, to_ent

        # Strategy 3: Union-level fallback
        from_cols = endpoints.get("from_collections") or []
        to_cols = endpoints.get("to_collections") or []
        from_ent = _resolve_single_entity(from_cols, collection_to_entities)
        to_ent = _resolve_single_entity(to_cols, collection_to_entities)
        return from_ent, to_ent

    # Dedicated edge collection
    from_cols = endpoints.get("from_collections") or []
    to_cols = endpoints.get("to_collections") or []
    from_ent = _resolve_single_entity(from_cols, collection_to_entities)
    to_ent = _resolve_single_entity(to_cols, collection_to_entities)
    return from_ent, to_ent


def _resolve_single_entity(
    cols: list[str], collection_to_entities: dict[str, list[str]]
) -> str:
    """Map a list of collections to a single entity type if unambiguous."""
    if not cols:
        return UNRESOLVED_ENDPOINT
    entity_types: set[str] = set()
    for c in cols:
        ents = collection_to_entities.get(c)
        if ents:
            entity_types.update(ents)
        else:
            entity_types.add(infer_entity_type_from_collection_name(c))
    if len(entity_types) == 1:
        return next(iter(entity_types))
    return UNRESOLVED_ENDPOINT


def _build_index_lookup(col: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """
    Build a field_name → index info lookup from the snapshot's index list.

    For compound indexes, only the first field gets the "indexed" flag since
    ArangoDB persistent indexes are prefix-usable.  All participating fields
    get listed in the compound entry.
    """
    lookup: dict[str, dict[str, Any]] = {}
    for idx in col.get("indexes") or []:
        if not isinstance(idx, dict):
            continue
        idx_type = idx.get("type", "")
        if idx_type == "primary":
            continue
        fields = idx.get("fields")
        if not isinstance(fields, list) or not fields:
            continue
        is_unique = bool(idx.get("unique"))
        is_sparse = bool(idx.get("sparse"))
        for i, f in enumerate(fields):
            if not isinstance(f, str):
                continue
            entry: dict[str, Any] = {
                "indexType": idx_type,
                "unique": is_unique,
                "sparse": is_sparse,
            }
            if len(fields) > 1:
                entry["compound"] = [str(x) for x in fields]
                entry["positionInCompound"] = i
            if f not in lookup:
                lookup[f] = entry
            elif is_unique and not lookup[f].get("unique"):
                lookup[f] = entry
    return lookup


def _extract_properties(
    col: dict[str, Any],
    type_value: str | None = None,
) -> list[dict[str, Any]]:
    """Extract property list from observed_fields in the snapshot."""
    observed = col.get("observed_fields") or {}
    fields: list[str] = []

    if type_value and isinstance(observed.get("by_type"), dict):
        fields = observed["by_type"].get(type_value, [])
    elif isinstance(observed.get("fields"), list):
        fields = observed["fields"]

    if not isinstance(fields, list):
        return []

    index_lookup = _build_index_lookup(col)

    props: list[dict[str, Any]] = []
    for f in fields:
        if not isinstance(f, str) or not f:
            continue
        prop: dict[str, Any] = {"name": f, "type": "string"}
        idx_info = index_lookup.get(f)
        if idx_info:
            prop["indexed"] = True
            if idx_info.get("unique"):
                prop["unique"] = True
        props.append(prop)
    return props


def _extract_indexes_for_mapping(col: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract the list of non-primary indexes from a collection entry for
    inclusion in the physical mapping.
    """
    result: list[dict[str, Any]] = []
    for idx in col.get("indexes") or []:
        if not isinstance(idx, dict):
            continue
        idx_type = idx.get("type", "")
        if idx_type == "primary":
            continue
        entry: dict[str, Any] = {
            "type": idx_type,
            "fields": [str(f) for f in idx.get("fields", []) if isinstance(f, str)],
        }
        if idx.get("unique"):
            entry["unique"] = True
        if idx.get("sparse"):
            entry["sparse"] = True
        name = idx.get("name")
        if isinstance(name, str) and name:
            entry["name"] = name
        result.append(entry)
    return result


def _build_property_mapping(props: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """
    Build a conceptual-property-name → physical-field-info mapping.

    Since the baseline uses field names directly as property names, the
    mapping is identity, but it records physical field name, index status,
    and uniqueness so downstream consumers (transpilers, query planners)
    can use it.
    """
    mapping: dict[str, dict[str, Any]] = {}
    for p in props:
        name = p.get("name")
        if not isinstance(name, str) or not name:
            continue
        entry: dict[str, Any] = {"physicalFieldName": name}
        if p.get("indexed"):
            entry["indexed"] = True
        if p.get("unique"):
            entry["unique"] = True
        mapping[name] = entry
    return mapping


def infer_baseline_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """
    Deterministic baseline inference from a physical schema snapshot.

    Detects LPG vs PG physical model style, extracts the correct ontology
    (entities + relationships), resolves domain/range from edge endpoints,
    and populates properties from observed fields.
    """
    cs = ConceptualSchema.empty()
    pm = PhysicalMapping.empty()
    detected_patterns: list[str] = []

    collections = snapshot.get("collections") or []
    if not isinstance(collections, list):
        collections = []

    # collection_name → [entity_type, ...] for endpoint resolution
    collection_to_entities: dict[str, list[str]] = {}

    # ── Entities ────────────────────────────────────────────────────────
    for col in collections:
        if not isinstance(col, dict):
            continue
        if col.get("type") != "document":
            continue

        collection_name = col.get("name")
        if not isinstance(collection_name, str) or not collection_name:
            continue

        col_indexes = _extract_indexes_for_mapping(col)

        type_field = _choose_type_field(col, is_edge=False)
        if type_field:
            if "LPG_LABEL" not in detected_patterns:
                detected_patterns.append("LPG_LABEL")
            for raw in _iter_type_values(col, type_field):
                ent_name = pascal_case(raw)
                props = _extract_properties(col, type_value=raw)
                cs.entities.append({"name": ent_name, "labels": [ent_name], "properties": props})
                ent_mapping: dict[str, Any] = {
                    "style": "LABEL",
                    "collectionName": collection_name,
                    "typeField": type_field,
                    "typeValue": raw,
                }
                if col_indexes:
                    ent_mapping["indexes"] = col_indexes
                prop_map = _build_property_mapping(props)
                if prop_map:
                    ent_mapping["properties"] = prop_map
                pm.entities[ent_name] = ent_mapping
                collection_to_entities.setdefault(collection_name, []).append(ent_name)
        else:
            if "PG_ENTITY_COLLECTION" not in detected_patterns:
                detected_patterns.append("PG_ENTITY_COLLECTION")
            ent_name = col.get("inferred_entity_type") or pascal_case(collection_name)
            props = _extract_properties(col)
            cs.entities.append({"name": ent_name, "labels": [ent_name], "properties": props})
            ent_mapping = {"style": "COLLECTION", "collectionName": collection_name}
            if col_indexes:
                ent_mapping["indexes"] = col_indexes
            prop_map = _build_property_mapping(props)
            if prop_map:
                ent_mapping["properties"] = prop_map
            pm.entities[ent_name] = ent_mapping
            collection_to_entities.setdefault(collection_name, []).append(ent_name)

    # ── Relationships ───────────────────────────────────────────────────
    # Also use graph definitions for PG endpoint resolution
    _enrich_endpoints_from_graphs(collections, snapshot, collection_to_entities)

    for col in collections:
        if not isinstance(col, dict):
            continue
        if col.get("type") != "edge":
            continue

        collection_name = col.get("name")
        if not isinstance(collection_name, str) or not collection_name:
            continue

        edge_indexes = _extract_indexes_for_mapping(col)

        type_field = _choose_type_field(col, is_edge=True)
        if type_field:
            if "LPG_GENERIC_EDGE" not in detected_patterns:
                detected_patterns.append("LPG_GENERIC_EDGE")
            for raw in _iter_type_values(col, type_field):
                rel_type = str(raw).strip()
                if not rel_type:
                    continue
                from_ent, to_ent = _resolve_endpoints(
                    col,
                    rel_type,
                    is_generic=True,
                    collection_to_entities=collection_to_entities,
                )
                props = _extract_properties(col, type_value=raw)
                cs.relationships.append(
                    {
                        "type": rel_type,
                        "fromEntity": from_ent,
                        "toEntity": to_ent,
                        "properties": props,
                    }
                )
                rel_mapping: dict[str, Any] = {
                    "style": "GENERIC_WITH_TYPE",
                    "collectionName": collection_name,
                    "typeField": type_field,
                    "typeValue": raw,
                }
                if edge_indexes:
                    rel_mapping["indexes"] = edge_indexes
                prop_map = _build_property_mapping(props)
                if prop_map:
                    rel_mapping["properties"] = prop_map
                pm.relationships[rel_type] = rel_mapping
        else:
            if "PG_DEDICATED_EDGE" not in detected_patterns:
                detected_patterns.append("PG_DEDICATED_EDGE")
            rel_type = col.get("inferred_relationship_type") or collection_name.upper()
            from_ent, to_ent = _resolve_endpoints(
                col,
                rel_type,
                is_generic=False,
                collection_to_entities=collection_to_entities,
            )
            props = _extract_properties(col)
            cs.relationships.append(
                {
                    "type": rel_type,
                    "fromEntity": from_ent,
                    "toEntity": to_ent,
                    "properties": props,
                }
            )
            rel_mapping = {"style": "DEDICATED_COLLECTION", "edgeCollectionName": collection_name}
            if edge_indexes:
                rel_mapping["indexes"] = edge_indexes
            prop_map = _build_property_mapping(props)
            if prop_map:
                rel_mapping["properties"] = prop_map
            pm.relationships[rel_type] = rel_mapping

    return {
        "conceptualSchema": cs.to_json(),
        "physicalMapping": pm.to_json(),
        "detectedPatterns": detected_patterns,
    }


def _enrich_endpoints_from_graphs(
    collections: list[dict[str, Any]],
    snapshot: dict[str, Any],
    collection_to_entities: dict[str, list[str]],
) -> None:
    """
    For PG-style edge collections without edge_endpoints data, fill in
    from/to collections from named graph edge_definitions.
    """
    graphs_detailed = snapshot.get("graphs_detailed") or []
    if not isinstance(graphs_detailed, list):
        return

    edge_def_map: dict[str, dict[str, list[str]]] = {}
    for g in graphs_detailed:
        if not isinstance(g, dict):
            continue
        for edef in g.get("edge_definitions") or []:
            if not isinstance(edef, dict):
                continue
            ec = edef.get("collection")
            if not ec:
                continue
            edge_def_map[ec] = {
                "from_collections": edef.get("from") or [],
                "to_collections": edef.get("to") or [],
            }

    for col in collections:
        if not isinstance(col, dict) or col.get("type") != "edge":
            continue
        cname = col.get("name", "")
        endpoints = col.get("edge_endpoints")
        if endpoints and (endpoints.get("from_collections") or endpoints.get("to_collections")):
            continue
        if cname in edge_def_map:
            col.setdefault("edge_endpoints", {}).update(edge_def_map[cname])
