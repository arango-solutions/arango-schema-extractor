from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from arango.database import StandardDatabase

import re

from .defaults import (
    MAX_BROADENED_TYPE_CANDIDATES,
    MAX_TYPE_FIELD_DISTINCT_VALUES,
    MAX_TYPE_VALUE_LENGTH,
    MIN_TYPE_FIELD_COVERAGE_FRACTION,
    MIN_TYPE_FIELD_DISTINCT_VALUES,
    SAMPLE_VALUE_TOP_K,
)
from .utils import pascal_case, sha256_hex, singularize, stable_dumps

logger = logging.getLogger(__name__)

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

_SYSTEM_FIELDS = frozenset({"_key", "_id", "_rev", "_from", "_to"})
_PROPERTY_SAMPLE_LIMIT = 10

PREFERRED_DOC_TYPE_FIELDS: list[str] = ["type", "_type", "kind", "entityType", "label"]
PREFERRED_EDGE_TYPE_FIELDS: list[str] = ["relation", "relType", "type"]


def infer_entity_type_from_collection_name(collection_name: str) -> str:
    return pascal_case(singularize(collection_name))


def infer_relationship_type_from_collection_name(collection_name: str) -> str:
    s = collection_name.replace("-", "_")
    return s.upper()


_DISCRIMINATOR_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_DISCRIMINATOR_VALUE_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
# Names ending in any of these are (almost) certainly identifiers, not types.
_ID_SUFFIXES: tuple[str, ...] = ("_id", "id", "_key", "key", "_uuid", "uuid", "_guid", "guid")


def _looks_like_discriminator_name(field: str) -> bool:
    """
    Heuristic: is this field name *plausibly* a type discriminator?

    We require a short, lowercase, snake_case identifier that is not obviously
    an ID/key/system field. Broadens the net past ``CANDIDATE_TYPE_KEYS`` so
    that collections whose type-tag field happens to be spelled ``category``,
    ``rel_kind``, ``etype`` etc. still get probed.
    """
    if not isinstance(field, str) or not field:
        return False
    if field.startswith("_"):
        return False
    lower = field.lower()
    if any(lower == suffix or lower.endswith(suffix) for suffix in _ID_SUFFIXES):
        return False
    return bool(_DISCRIMINATOR_NAME_RE.match(field))


def _detect_candidate_type_fields(sample: dict[str, Any]) -> list[str]:
    """
    Enumerate candidate type-discriminator fields from a sample document.

    First the allow-listed names from ``CANDIDATE_TYPE_KEYS`` (fast path,
    preserves deterministic ordering for fixtures), then an additional batch
    of name-pattern matches capped at ``MAX_BROADENED_TYPE_CANDIDATES`` so
    AQL cost stays bounded.
    """
    if not isinstance(sample, dict):
        return []

    allow_listed: list[str] = [k for k in CANDIDATE_TYPE_KEYS if k in sample]
    already = set(allow_listed)

    broadened: list[str] = []
    for k, v in sample.items():
        if k in already:
            continue
        if not _looks_like_discriminator_name(k):
            continue
        if not isinstance(v, str):
            continue
        if len(v) > MAX_TYPE_VALUE_LENGTH or not _DISCRIMINATOR_VALUE_RE.match(v):
            continue
        broadened.append(k)
        if len(broadened) >= MAX_BROADENED_TYPE_CANDIDATES:
            break

    return allow_listed + broadened


def _iter_scalar_values(v: Any):
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


def _pick_best_type_field(
    entry: dict[str, Any], *, is_edge: bool = False
) -> str | None:
    """Pick the best type-discriminator field from snapshot stats.

    Acceptance rules (any failure disqualifies the field):

    * Distinct value count in
      ``[MIN_TYPE_FIELD_DISTINCT_VALUES, MAX_TYPE_FIELD_DISTINCT_VALUES]``.
      The upper bound rejects high-cardinality ID-like fields that happen
      to pass name-pattern checks (e.g. ``comment_id`` with one distinct
      value per document).
    * Coverage fraction of the top-K observed distinct values, relative to
      the collection's document ``count``, is at least
      ``MIN_TYPE_FIELD_COVERAGE_FRACTION``. Fields whose non-null coverage
      is sparse do not look like a type tag.
    * Every observed value is a string of at most ``MAX_TYPE_VALUE_LENGTH``
      characters matching ``[A-Za-z0-9_-]+``. Free-form content is not a
      type label.

    Among the candidates that pass, the field with the most distinct values
    wins (tie-broken by the preferred-name ordering).

    Edge special case — a single-distinct-value field is accepted as a
    discriminator when that single value differs from both the collection
    name and its derived relationship type. This disambiguates a genuine
    generic-but-currently-single-type edge collection from a dedicated PG
    edge that carries a redundant metadata field (e.g. ``mentions`` edges
    with ``relation = "mentions"``).
    """
    candidates = entry.get("candidate_type_fields") or []
    value_counts = entry.get("sample_field_value_counts") or {}
    total_docs = int(entry.get("count") or 0)
    preferred = PREFERRED_EDGE_TYPE_FIELDS if is_edge else PREFERRED_DOC_TYPE_FIELDS
    ordered = [c for c in preferred if c in candidates] + [c for c in candidates if c not in preferred]

    best: str | None = None
    best_n = 0
    for f in ordered:
        items = value_counts.get(f)
        if not _passes_distribution_shape(items, total_docs):
            continue
        n = len({str(it["value"]) for it in items if isinstance(it, dict) and "value" in it})
        if n > best_n:
            best = f
            best_n = n

    if best is not None and MIN_TYPE_FIELD_DISTINCT_VALUES <= best_n <= MAX_TYPE_FIELD_DISTINCT_VALUES:
        return best

    if is_edge:
        single = _single_value_edge_fallback(entry, value_counts, ordered)
        if single:
            return single

    return None


def _passes_distribution_shape(
    items: Any,
    total_docs: int,
) -> bool:
    """
    Gate a candidate field on its observed value distribution.

    Returns True iff:
      * ``items`` is a list of ``{"value", "count"}`` dicts,
      * every value is a string matching the discriminator shape rule,
      * the distinct count is within
        ``[MIN_TYPE_FIELD_DISTINCT_VALUES, MAX_TYPE_FIELD_DISTINCT_VALUES]``,
      * the sum of observed counts covers at least
        ``MIN_TYPE_FIELD_COVERAGE_FRACTION`` of ``total_docs``
        (or ``total_docs`` is unknown / 0, in which case we accept based on
        distinct count alone since we can't compute the ratio).
    """
    if not isinstance(items, list) or not items:
        return False
    distinct: set[str] = set()
    observed_count = 0
    for it in items:
        if not isinstance(it, dict) or "value" not in it:
            return False
        v = it["value"]
        if not isinstance(v, str):
            return False
        if len(v) > MAX_TYPE_VALUE_LENGTH or not _DISCRIMINATOR_VALUE_RE.match(v):
            return False
        distinct.add(v)
        c = it.get("count")
        if isinstance(c, int) and c > 0:
            observed_count += c
    n_distinct = len(distinct)
    if n_distinct < MIN_TYPE_FIELD_DISTINCT_VALUES:
        # Single-value fallback is handled separately upstream.
        return n_distinct == 1
    if n_distinct > MAX_TYPE_FIELD_DISTINCT_VALUES:
        return False
    if total_docs > 0 and observed_count > 0:
        if (observed_count / total_docs) < MIN_TYPE_FIELD_COVERAGE_FRACTION:
            return False
    return True


def _single_value_edge_fallback(
    entry: dict[str, Any],
    value_counts: dict[str, Any],
    ordered: list[str],
) -> str | None:
    """Accept a single-distinct-value field on an edge collection when its
    value is not just the collection name echoed back (see docstring on
    ``_pick_best_type_field``)."""
    for f in ordered:
        items = value_counts.get(f)
        if not isinstance(items, list) or len(items) != 1:
            continue
        first = items[0]
        if not isinstance(first, dict) or "value" not in first:
            continue
        raw = first["value"]
        if not isinstance(raw, str):
            continue
        single_val = raw.strip().upper().replace("-", "_")
        col_name = str(entry.get("name", "")).strip().upper().replace("-", "_")
        derived = infer_relationship_type_from_collection_name(entry.get("name", ""))
        if single_val and single_val != col_name and single_val != derived:
            return f
    return None


def _type_values_for_field(entry: dict[str, Any], field: str) -> list[str]:
    """Extract sorted distinct type values for a field from snapshot stats."""
    items = (entry.get("sample_field_value_counts") or {}).get(field)
    if not isinstance(items, list):
        return []
    return sorted({str(it["value"]).strip() for it in items if isinstance(it, dict) and "value" in it and str(it["value"]).strip()})


def _detect_type_fields_via_collect(
    db: StandardDatabase,
    collection_name: str,
) -> tuple[list[str], dict[str, list[dict[str, Any]]]]:
    """
    Detect type discriminator fields using AQL COLLECT for accurate counting.
    Unlike LIMIT-based sampling, this scans all documents and finds every distinct value.
    """
    try:
        cursor = db.aql.execute(
            "FOR d IN @@c LIMIT 1 RETURN d",
            bind_vars={"@c": collection_name},
        )
        samples = list(cursor)
    except Exception:
        return [], {}

    if not samples:
        return [], {}

    sample = samples[0] if isinstance(samples[0], dict) else {}
    candidates = _detect_candidate_type_fields(sample)
    if not candidates:
        return [], {}

    value_counts: dict[str, list[dict[str, Any]]] = {}
    for key in candidates:
        try:
            cursor = db.aql.execute(
                "FOR d IN @@c "
                "COLLECT val = d[@field] WITH COUNT INTO cnt "
                "FILTER val != null "
                "SORT cnt DESC LIMIT @top "
                "RETURN {value: val, count: cnt}",
                bind_vars={"@c": collection_name, "field": key, "top": SAMPLE_VALUE_TOP_K},
            )
            items = list(cursor)
            if items:
                value_counts[key] = items
        except Exception:
            continue

    return candidates, value_counts


def _detect_observed_fields(
    db: StandardDatabase,
    collection_name: str,
    *,
    type_field: str | None = None,
    type_values: list[str] | None = None,
    is_edge: bool = False,
) -> dict[str, Any]:
    """
    Detect property fields for a collection by sampling document attributes.
    For type-discriminated collections, detects per type value.
    """
    exclude = set(_SYSTEM_FIELDS)
    if type_field:
        exclude.add(type_field)
        exclude.add("labels")

    if type_field and type_values:
        result: dict[str, list[str]] = {}
        for tv in type_values:
            try:
                cursor = db.aql.execute(
                    "FOR d IN @@c FILTER d[@field] == @val "
                    "LIMIT @lim RETURN ATTRIBUTES(d)",
                    bind_vars={
                        "@c": collection_name,
                        "field": type_field,
                        "val": tv,
                        "lim": _PROPERTY_SAMPLE_LIMIT,
                    },
                )
                keys: set[str] = set()
                for attr_list in cursor:
                    if isinstance(attr_list, list):
                        keys.update(k for k in attr_list if k not in exclude)
                result[str(tv)] = sorted(keys)
            except Exception:
                continue
        return {"by_type": result}
    else:
        try:
            cursor = db.aql.execute(
                "FOR d IN @@c LIMIT @lim RETURN ATTRIBUTES(d)",
                bind_vars={"@c": collection_name, "lim": _PROPERTY_SAMPLE_LIMIT},
            )
            keys_all: set[str] = set()
            for attr_list in cursor:
                if isinstance(attr_list, list):
                    keys_all.update(k for k in attr_list if k not in exclude)
            return {"fields": sorted(keys_all)}
        except Exception:
            return {}


def _detect_edge_endpoints(
    db: StandardDatabase,
    edge_collection_name: str,
    *,
    rel_type_field: str | None = None,
    doc_type_info: dict[str, tuple[str, set[str]]],
) -> dict[str, Any]:
    """
    Detect _from/_to endpoint collections and resolve entity types.

    Three resolution strategies depending on what's available:
    1. LPG endpoints (doc collections have type discriminators): DOCUMENT()
       lookups resolve per-relation entity types.
    2. Hybrid/PG endpoints with generic edge collection: COLLECT by relation
       type + from/to collection, then map collection → entity type.
    3. No relation type field: just report from/to collections.
    """
    try:
        cursor = db.aql.execute(
            "FOR e IN @@c "
            "COLLECT fromCol = PARSE_IDENTIFIER(e._from).collection, "
            "toCol = PARSE_IDENTIFIER(e._to).collection "
            "RETURN {fromCollection: fromCol, toCollection: toCol}",
            bind_vars={"@c": edge_collection_name},
        )
        all_from_cols: set[str] = set()
        all_to_cols: set[str] = set()
        for item in cursor:
            if isinstance(item, dict):
                fc = item.get("fromCollection")
                tc = item.get("toCollection")
                if fc:
                    all_from_cols.add(fc)
                if tc:
                    all_to_cols.add(tc)
    except Exception:
        return {}

    result: dict[str, Any] = {
        "from_collections": sorted(all_from_cols),
        "to_collections": sorted(all_to_cols),
    }

    if not rel_type_field:
        return result

    any_lpg_endpoint = any(c in doc_type_info for c in all_from_cols | all_to_cols)

    if any_lpg_endpoint:
        # Strategy 1: LPG — resolve entity types via DOCUMENT() lookups
        node_type_field = None
        for col_name in sorted(all_from_cols | all_to_cols):
            if col_name in doc_type_info:
                node_type_field = doc_type_info[col_name][0]
                break

        if node_type_field:
            try:
                cursor = db.aql.execute(
                    "FOR e IN @@ec "
                    "LET fromDoc = DOCUMENT(e._from) "
                    "LET toDoc = DOCUMENT(e._to) "
                    "COLLECT relType = e[@relField], "
                    "fromType = fromDoc[@nodeTypeField], "
                    "toType = toDoc[@nodeTypeField] "
                    "RETURN {relType: relType, fromType: fromType, toType: toType}",
                    bind_vars={
                        "@ec": edge_collection_name,
                        "relField": rel_type_field,
                        "nodeTypeField": node_type_field,
                    },
                )
                endpoints_by_type: dict[str, dict[str, set[str]]] = {}
                for item in cursor:
                    if not isinstance(item, dict):
                        continue
                    rt = item.get("relType")
                    ft = item.get("fromType")
                    tt = item.get("toType")
                    if rt:
                        rt_str = str(rt)
                        endpoints_by_type.setdefault(rt_str, {"from": set(), "to": set()})
                        if ft:
                            endpoints_by_type[rt_str]["from"].add(str(ft))
                        if tt:
                            endpoints_by_type[rt_str]["to"].add(str(tt))

                result["entity_types_by_relation"] = {
                    rt: {
                        "from_entity_types": sorted(ep["from"]),
                        "to_entity_types": sorted(ep["to"]),
                    }
                    for rt, ep in sorted(endpoints_by_type.items())
                }
                return result
            except Exception as exc:
                logger.debug("LPG endpoint resolution failed for %s: %s", edge_collection_name, exc)

    # Strategy 2: Hybrid/PG — resolve per-relation endpoints by collection
    # name (no DOCUMENT() needed, just PARSE_IDENTIFIER).
    try:
        cursor = db.aql.execute(
            "FOR e IN @@ec "
            "COLLECT relType = e[@relField], "
            "fromCol = PARSE_IDENTIFIER(e._from).collection, "
            "toCol = PARSE_IDENTIFIER(e._to).collection "
            "RETURN {relType: relType, fromCol: fromCol, toCol: toCol}",
            bind_vars={
                "@ec": edge_collection_name,
                "relField": rel_type_field,
            },
        )
        cols_by_type: dict[str, dict[str, set[str]]] = {}
        for item in cursor:
            if not isinstance(item, dict):
                continue
            rt = item.get("relType")
            fc = item.get("fromCol")
            tc = item.get("toCol")
            if rt:
                rt_str = str(rt)
                cols_by_type.setdefault(rt_str, {"from": set(), "to": set()})
                if fc:
                    cols_by_type[rt_str]["from"].add(str(fc))
                if tc:
                    cols_by_type[rt_str]["to"].add(str(tc))

        result["collections_by_relation"] = {
            rt: {
                "from_collections": sorted(ep["from"]),
                "to_collections": sorted(ep["to"]),
            }
            for rt, ep in sorted(cols_by_type.items())
        }
    except Exception as exc:
        logger.debug("Per-relation collection resolution failed for %s: %s", edge_collection_name, exc)

    return result


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

    Always detects type discriminator fields (via AQL COLLECT) and edge endpoint
    collections regardless of sample_limit_per_collection, so the baseline can
    correctly classify PG vs LPG physical model styles.
    """
    collections_info = db.collections()
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
                        continue
    else:
        collections = {}
    snapshot: dict[str, Any] = {
        "version": 1,
        "generated_at": None,
        "collections": [],
        "graphs": [],
    }

    # ── Phase 1: Collect metadata for every collection ──────────────────
    entries: list[dict[str, Any]] = []
    for name in sorted(collections.keys()):
        col = collections[name]
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

        entries.append(entry)

    # ── Phase 2: Detect type discriminators for ALL collections (COLLECT) ──
    for entry in entries:
        candidates, value_counts = _detect_type_fields_via_collect(db, entry["name"])
        entry["candidate_type_fields"] = candidates
        entry["sample_field_value_counts"] = value_counts

    # ── Phase 3: Build doc type field map for edge endpoint resolution ──
    doc_type_info: dict[str, tuple[str, set[str]]] = {}
    for entry in entries:
        if entry["type"] != "document":
            continue
        best_field = _pick_best_type_field(entry, is_edge=False)
        if best_field:
            values = set(_type_values_for_field(entry, best_field))
            if values:
                doc_type_info[entry["name"]] = (best_field, values)

    # ── Phase 4: Detect edge endpoints and property fields ──────────────
    for entry in entries:
        is_edge = entry["type"] == "edge"
        best_field = _pick_best_type_field(entry, is_edge=is_edge)
        type_values = _type_values_for_field(entry, best_field) if best_field else None

        if is_edge:
            entry["edge_endpoints"] = _detect_edge_endpoints(
                db,
                entry["name"],
                rel_type_field=best_field,
                doc_type_info=doc_type_info,
            )

        entry["observed_fields"] = _detect_observed_fields(
            db,
            entry["name"],
            type_field=best_field,
            type_values=type_values,
            is_edge=is_edge,
        )

    # ── Phase 5: Full document sampling (optional) ──────────────────────
    if sample_limit_per_collection and sample_limit_per_collection > 0:
        for entry in entries:
            cname = entry["name"]
            is_edge = entry["type"] == "edge"
            try:
                cursor = db.aql.execute(
                    "FOR d IN @@c LIMIT @limit RETURN d",
                    bind_vars={"@c": cname, "limit": int(sample_limit_per_collection)},
                )
                samples = list(cursor)
                if include_samples_in_snapshot:
                    if is_edge:
                        entry["sample_edges"] = samples
                    else:
                        entry["sample_documents"] = samples
            except Exception as e:
                entry["sample_error"] = str(e)

    snapshot["collections"] = entries

    # ── Named graphs (best-effort) ──────────────────────────────────────
    try:
        graphs = db.graphs()
        snapshot["graphs"] = graphs
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
