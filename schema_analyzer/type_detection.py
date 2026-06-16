"""Deterministic type-discriminator heuristics for the physical snapshot.

Pure helpers extracted from ``snapshot.py``: given sample documents and the
per-field value distributions the snapshot collects, decide which field (if
any) acts as a type discriminator, and derive conceptual names from collection
names. No database access — every function operates on plain dicts/strings —
so they are cheap to unit-test in isolation.

``snapshot.py`` re-exports these names, so existing imports such as
``from schema_analyzer.snapshot import _pick_best_type_field`` keep working.
"""

from __future__ import annotations

import re
from typing import Any

from .defaults import (
    MAX_BROADENED_TYPE_CANDIDATES,
    MAX_TYPE_FIELD_DISTINCT_VALUES,
    MAX_TYPE_VALUE_LENGTH,
    MIN_TYPE_FIELD_COVERAGE_FRACTION,
    MIN_TYPE_FIELD_DISTINCT_VALUES,
)
from .utils import pascal_case, singularize

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


def _pick_best_type_field(entry: dict[str, Any], *, is_edge: bool = False) -> str | None:
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
        n = len({str(it["value"]) for it in (items or []) if isinstance(it, dict) and "value" in it})
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
    return not (
        total_docs > 0 and observed_count > 0 and (observed_count / total_docs) < MIN_TYPE_FIELD_COVERAGE_FRACTION
    )


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
    return sorted(
        {
            str(it["value"]).strip()
            for it in items
            if isinstance(it, dict) and "value" in it and str(it["value"]).strip()
        }
    )
