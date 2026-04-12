from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from arango.database import StandardDatabase

from .defaults import SAMPLE_VALUE_TOP_K
from .utils import pascal_case, sha256_hex, singularize, stable_dumps

CANDIDATE_TYPE_KEYS: list[str] = [
    "type",
    "_type",
    "label",
    "labels",
    "kind",
    "entityType",
    "relation",
    "relType",
]


def infer_entity_type_from_collection_name(collection_name: str) -> str:
    return pascal_case(singularize(collection_name))


def infer_relationship_type_from_collection_name(collection_name: str) -> str:
    # Edge collections in generator use rel_type.lower() which may contain underscores already.
    s = collection_name.replace("-", "_")
    return s.upper()


def _detect_candidate_type_fields(sample: dict[str, Any]) -> list[str]:
    if not isinstance(sample, dict):
        return []
    return [k for k in CANDIDATE_TYPE_KEYS if k in sample]


def _iter_scalar_values(v: Any):
    # Yield scalar/string-ish values from a field.
    if v is None:
        return
    if isinstance(v, (str, int, float, bool)):
        yield v
        return
    if isinstance(v, list):
        for x in v:
            if isinstance(x, (str, int, float, bool)):
                yield x
        return


def _omit_samples(snapshot: dict[str, Any]) -> dict[str, Any]:
    clone = {**snapshot}
    # generated_at is intentionally excluded from fingerprints/caching because it is time-varying
    # and does not represent the underlying physical schema.
    if "generated_at" in clone:
        clone["generated_at"] = None
    cols = []
    for c in clone.get("collections", []) or []:
        if not isinstance(c, dict):
            continue
        c2 = dict(c)
        c2.pop("sample_documents", None)
        c2.pop("sample_edges", None)
        cols.append(c2)
    clone["collections"] = cols
    return clone


def fingerprint_physical_schema(snapshot: dict[str, Any], *, include_samples: bool = False) -> str:
    # Even when including samples, normalize away generated_at to keep fingerprints stable.
    data = dict(snapshot) if isinstance(snapshot, dict) else {"raw": snapshot}
    if "generated_at" in data:
        data["generated_at"] = None
    if not include_samples:
        data = _omit_samples(data)
    return sha256_hex(stable_dumps(data))


def _normalize_index(idx: Any) -> dict[str, Any]:
    if not isinstance(idx, dict):
        return {"raw": idx}
    # Keep a stable subset; preserve remaining keys for forward compatibility.
    normalized = dict(idx)
    # Normalize fields ordering if present
    fields = normalized.get("fields")
    if isinstance(fields, list):
        normalized["fields"] = list(fields)
    return normalized


def _sort_indexes(indexes: list[Any]) -> list[Any]:
    def key(idx: Any):
        if not isinstance(idx, dict):
            return ("", "", "")
        return (
            str(idx.get("type") or ""),
            ",".join([str(x) for x in (idx.get("fields") or [])]) if isinstance(idx.get("fields"), list) else "",
            str(idx.get("name") or idx.get("id") or ""),
        )

    return sorted([_normalize_index(i) for i in indexes], key=key)


def _summarize_graph_props(props: Any) -> dict[str, Any]:
    """
    Extract stable, high-signal graph details:
    - edgeDefinitions (collection, from[], to[])
    - orphanCollections
    """
    if not isinstance(props, dict):
        return {"raw": props}
    out: dict[str, Any] = {}
    if "name" in props:
        out["name"] = props.get("name")
    if "edgeDefinitions" in props and isinstance(props["edgeDefinitions"], list):
        defs = []
        for d in props["edgeDefinitions"]:
            if not isinstance(d, dict):
                continue
            defs.append(
                {
                    "collection": d.get("collection"),
                    "from": sorted(list(d.get("from", []) or [])),
                    "to": sorted(list(d.get("to", []) or [])),
                }
            )
        out["edge_definitions"] = sorted(defs, key=lambda x: str(x.get("collection") or ""))
    if "orphanCollections" in props and isinstance(props["orphanCollections"], list):
        out["orphan_collections"] = sorted(list(props["orphanCollections"]))
    return out


def snapshot_physical_schema(
    db: StandardDatabase,
    *,
    sample_limit_per_collection: int = 0,
    include_samples_in_snapshot: bool = False,
) -> dict[str, Any]:
    """
    Deterministic physical schema snapshot using python-arango Database.
    """
    collections_info = db.collections()
    # python-arango may return either a dict(name->collection) or a list of collection info dicts.
    if isinstance(collections_info, dict):
        collections = collections_info
    elif isinstance(collections_info, list):
        collections = {}
        for item in collections_info:
            if isinstance(item, dict):
                name = item.get("name")
                if isinstance(name, str) and name:
                    try:
                        collections[name] = db.collection(name)
                    except Exception:
                        # If we can't materialize the collection object, skip it.
                        continue
    else:
        collections = {}
    snapshot: dict[str, Any] = {
        "version": 1,
        "generated_at": None,
        "collections": [],
        "graphs": [],
    }

    # Avoid importing datetime in hot paths; keep deterministic by caller if desired.
    # We'll set generated_at at analyzer-level; snapshotter focuses on structure.

    for name in sorted(collections.keys()):
        col = collections[name]
        # Skip system collections
        if name.startswith("_"):
            continue

        try:
            props = col.properties()
        except Exception as e:
            props = {"error": str(e)}

        try:
            count = col.count()
        except Exception:
            count = None

        try:
            indexes = _sort_indexes(list(col.indexes() or []))
        except Exception:
            indexes = []

        is_edge = bool(props.get("type") == 3) if isinstance(props, dict) else False

        entry: dict[str, Any] = {
            "name": name,
            "type": "edge" if is_edge else "document",
            "count": count,
            "properties": props,
            "indexes": indexes,
            "candidate_type_fields": [],
        }
        if is_edge:
            entry["inferred_relationship_type"] = infer_relationship_type_from_collection_name(name)
        else:
            entry["inferred_entity_type"] = infer_entity_type_from_collection_name(name)

        if sample_limit_per_collection and sample_limit_per_collection > 0:
            try:
                cursor = db.aql.execute(
                    "FOR d IN @@c LIMIT @limit RETURN d",
                    bind_vars={"@c": name, "limit": int(sample_limit_per_collection)},
                )
                samples = list(cursor)
                if include_samples_in_snapshot:
                    if is_edge:
                        entry["sample_edges"] = samples
                    else:
                        entry["sample_documents"] = samples

                field_stats: dict[str, int] = {}
                value_stats: dict[str, dict[str, int]] = {}
                for s in samples:
                    sd = s if isinstance(s, dict) else {}
                    for f in _detect_candidate_type_fields(sd):
                        field_stats[f] = field_stats.get(f, 0) + 1
                        for val in _iter_scalar_values(sd.get(f)):
                            sval = str(val)
                            value_stats.setdefault(f, {})
                            value_stats[f][sval] = value_stats[f].get(sval, 0) + 1

                entry["candidate_type_field_counts"] = {
                    k: field_stats[k] for k in sorted(field_stats.keys(), key=lambda x: (-field_stats[x], x))
                }
                entry["candidate_type_fields"] = list(entry["candidate_type_field_counts"].keys())

                # Summarize observed values for candidate type fields.
                # Include even when samples are omitted, so the analyzer can infer types.
                top_k = SAMPLE_VALUE_TOP_K
                field_value_counts = {}
                for f in entry["candidate_type_fields"]:
                    counts = value_stats.get(f, {})
                    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top_k]
                    if items:
                        field_value_counts[f] = [{"value": v, "count": c} for v, c in items]
                entry["sample_field_value_counts"] = field_value_counts
            except Exception as e:
                entry["sample_error"] = str(e)

        snapshot["collections"].append(entry)

    # Named graphs (best-effort)
    try:
        graphs = db.graphs()
        snapshot["graphs"] = graphs
        # Try to fetch deeper properties per graph when possible.
        detailed = []
        if isinstance(graphs, list):
            for g in graphs:
                name = None
                if isinstance(g, str):
                    name = g
                elif isinstance(g, dict):
                    name = g.get("name") or g.get("_key") or g.get("id")
                if not name:
                    continue
                try:
                    gp = db.graph(name).properties()
                    detailed.append(_summarize_graph_props(gp))
                except Exception as e:
                    detailed.append({"name": name, "error": str(e)})
        snapshot["graphs_detailed"] = sorted(detailed, key=lambda x: str(x.get("name") or ""))
    except Exception as e:
        snapshot["graphs_error"] = str(e)

    return snapshot
