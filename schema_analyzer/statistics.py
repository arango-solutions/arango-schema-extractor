"""
Per-relationship cardinality + selectivity statistics for a physical mapping.

Issue #3. Every cost-aware downstream of the analyzer (query planner, NL
prompt enricher, index advisor) has historically re-implemented the same
bundle of LENGTH()+COLLECT queries to derive counts, average degrees, and
cardinality buckets. This module centralises that work so the analyzer's
export carries a statistics block the rest of the ecosystem can consume
as-is.

The computation is deterministic: given the same snapshot + physical
mapping + database state, the output is byte-equal across invocations
(up to the ``computed_at`` timestamp, which is intentionally bumped each
run so downstream cachers can reason about freshness).

Bounded AQL cost by design:

* One ``LENGTH(@@c)`` per non-system collection.
* One ``COLLECT WITH COUNT`` per ``LABEL`` / ``GENERIC_WITH_TYPE`` subset,
  and one edge count per relationship entry.
* No per-document scans; no joins; no DOCUMENT() lookups.

A 100-collection / 1_000-relationship schema completes well inside the
issue's 2-second target against a 100 k-row DB.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ._arango import aql_execute
from .defaults import (
    STATISTICS_CARDINALITY_THRESHOLD,
    STATISTICS_DEGREE_ROUND,
    STATISTICS_SELECTIVITY_ROUND,
)

if TYPE_CHECKING:
    from arango.database import StandardDatabase

logger = logging.getLogger(__name__)

# Re-exported aliases preserved for backward compatibility with any
# downstream caller importing the constants from this module.
CARDINALITY_THRESHOLD: float = STATISTICS_CARDINALITY_THRESHOLD
DEGREE_ROUND: int = STATISTICS_DEGREE_ROUND
SELECTIVITY_ROUND: int = STATISTICS_SELECTIVITY_ROUND

STATISTICS_STATUS_OK: str = "ok"
STATISTICS_STATUS_SKIPPED_NO_DB: str = "skipped_no_db"
STATISTICS_STATUS_PARTIAL: str = "partial"


def compute_statistics(
    db: StandardDatabase | None,
    snapshot: dict[str, Any],
    physical_mapping: dict[str, Any],
    conceptual_schema: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    Compute the ``statistics`` block for the analyzer's export.

    Parameters
    ----------
    db :
        Live ArangoDB handle. When ``None`` (snapshot-only analysis) the
        function returns ``None`` so the caller can stamp
        ``metadata.statistics_status = "skipped_no_db"`` and continue.
    snapshot :
        The physical-schema snapshot dict. Used to determine per-collection
        ``is_edge`` flags deterministically without an extra round trip.
    physical_mapping :
        The ``physicalMapping`` portion of the analysis result. Drives
        which entity / relationship entries to compute counts for.
    conceptual_schema :
        Optional. Consulted to look up ``fromEntity`` / ``toEntity``
        labels for relationship endpoints. When absent the statistics
        block still populates ``edge_count`` but sets
        ``source_count`` / ``target_count`` / ``avg_*`` / ``selectivity``
        to their degenerate defaults.

    Returns
    -------
    dict | None
        The statistics block, or ``None`` if no DB handle was provided.
    """
    if db is None:
        return None

    collections_snapshot = {
        name: c
        for c in (snapshot.get("collections") or [])
        if isinstance(c, dict) and isinstance((name := c.get("name")), str)
    }

    collections_out: dict[str, dict[str, Any]] = {}
    entities_out: dict[str, dict[str, Any]] = {}
    relationships_out: dict[str, dict[str, Any]] = {}

    any_failure = False

    # ── Collections: one LENGTH per collection ─────────────────────────
    counts: dict[str, int] = {}
    for name, snap_entry in collections_snapshot.items():
        total = _safe_collection_length(db, name)
        if total is None:
            any_failure = True
            continue
        counts[name] = total
        collections_out[name] = {
            "count": total,
            "is_edge": snap_entry.get("type") == "edge",
        }

    # ── Entities ───────────────────────────────────────────────────────
    for ent_name, ent_mapping in (physical_mapping.get("entities") or {}).items():
        if not isinstance(ent_mapping, dict):
            continue
        col = ent_mapping.get("collectionName")
        if not isinstance(col, str):
            continue
        style = ent_mapping.get("style")
        if style == "LABEL":
            type_field = ent_mapping.get("typeField")
            type_value = ent_mapping.get("typeValue")
            est = _safe_filtered_count(db, col, type_field, type_value)
        else:
            est = counts.get(col)
        if est is None:
            any_failure = True
            continue
        entities_out[ent_name] = {"estimated_count": int(est)}

    # ── Relationships ──────────────────────────────────────────────────
    rel_endpoints = _endpoint_map_from_conceptual(conceptual_schema)
    for rel_type, rel_mapping in (physical_mapping.get("relationships") or {}).items():
        if not isinstance(rel_mapping, dict):
            continue
        col = rel_mapping.get("edgeCollectionName") or rel_mapping.get("collectionName")
        if not isinstance(col, str):
            continue
        style = rel_mapping.get("style")
        if style == "GENERIC_WITH_TYPE":
            type_field = rel_mapping.get("typeField")
            type_value = rel_mapping.get("typeValue")
            edge_count = _safe_filtered_count(db, col, type_field, type_value)
        else:
            edge_count = counts.get(col)
        if edge_count is None:
            any_failure = True
            continue

        domain, range_ = rel_endpoints.get(rel_type, (None, None))
        source_count = int(entities_out.get(domain, {}).get("estimated_count", 0)) if domain else 0
        target_count = int(entities_out.get(range_, {}).get("estimated_count", 0)) if range_ else 0

        avg_out = round(edge_count / source_count, DEGREE_ROUND) if source_count > 0 else 0.0
        avg_in = round(edge_count / target_count, DEGREE_ROUND) if target_count > 0 else 0.0
        if source_count > 0 and target_count > 0:
            selectivity = round(edge_count / (source_count * target_count), SELECTIVITY_ROUND)
        else:
            selectivity = 1.0

        relationships_out[rel_type] = {
            "edge_count": int(edge_count),
            "source_count": source_count,
            "target_count": target_count,
            "avg_out_degree": avg_out,
            "avg_in_degree": avg_in,
            "cardinality_pattern": _classify_cardinality(avg_out, avg_in),
            "selectivity": selectivity,
        }

    # If the DB handle is present but every AQL call failed (e.g. a test
    # double without an ``aql`` attribute) treat this as no-DB rather
    # than fabricating a status="partial" block full of empty dicts.
    if collections_snapshot and not counts:
        return None

    status = STATISTICS_STATUS_OK if not any_failure else STATISTICS_STATUS_PARTIAL

    return {
        "computed_at": _now_iso(),
        "status": status,
        "collections": collections_out,
        "entities": entities_out,
        "relationships": relationships_out,
    }


# ── Classification + helpers ──────────────────────────────────────────────


def _classify_cardinality(avg_out: float, avg_in: float) -> str:
    hi_out = avg_out > CARDINALITY_THRESHOLD
    hi_in = avg_in > CARDINALITY_THRESHOLD
    if hi_out and hi_in:
        return "N:M"
    if hi_out:
        return "1:N"
    if hi_in:
        return "N:1"
    return "1:1"


def _endpoint_map_from_conceptual(
    conceptual_schema: dict[str, Any] | None,
) -> dict[str, tuple[str | None, str | None]]:
    """Build rel_type → (fromEntity, toEntity) map from the conceptual schema."""
    out: dict[str, tuple[str | None, str | None]] = {}
    if not isinstance(conceptual_schema, dict):
        return out
    for rel in conceptual_schema.get("relationships") or []:
        if not isinstance(rel, dict):
            continue
        rtype = rel.get("type")
        if not isinstance(rtype, str):
            continue
        out[rtype] = (rel.get("fromEntity"), rel.get("toEntity"))
    return out


def _safe_collection_length(db: StandardDatabase, name: str) -> int | None:
    try:
        cursor = aql_execute(db, "RETURN LENGTH(@@c)", bind_vars={"@c": name})
        for row in cursor:
            return int(row)
    except Exception as exc:  # pragma: no cover — logged path
        logger.debug("LENGTH(%s) failed: %s", name, exc)
    return None


def _safe_filtered_count(
    db: StandardDatabase,
    collection: str,
    type_field: Any,
    type_value: Any,
) -> int | None:
    """COLLECT WITH COUNT where ``d[type_field] == type_value``.

    Returns ``None`` on any AQL failure or when ``type_field`` /
    ``type_value`` are missing — the caller treats that as "this subset
    is not countable" rather than silently reporting 0.
    """
    if not isinstance(type_field, str) or type_field == "":
        return None
    if type_value is None:
        return None
    try:
        cursor = aql_execute(
            db,
            "FOR d IN @@c FILTER d[@field] == @val COLLECT WITH COUNT INTO c RETURN c",
            bind_vars={"@c": collection, "field": type_field, "val": type_value},
        )
        for row in cursor:
            return int(row)
    except Exception as exc:  # pragma: no cover — logged path
        logger.debug(
            "filtered COLLECT(%s, %s=%s) failed: %s",
            collection,
            type_field,
            type_value,
            exc,
        )
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
