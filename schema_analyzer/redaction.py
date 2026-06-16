"""Redaction of physical snapshots before LLM egress (PRD §4.3).

When an LLM provider is configured, the physical schema snapshot — which can
include sampled documents and sampled field-value distributions — is sent to a
third-party API. That is customer-configured data egress, and some deployments
need to scrub the actual *data values* first while still letting the model see
the *structure* (collections, field names, indexes) it needs to infer a good
conceptual model.

Redaction is applied only to the copy of the snapshot handed to
``_build_prompt``; the local snapshot used for fingerprinting, baseline
inference, reconciliation, and statistics is always the unredacted original, so
output quality and grounding are unaffected by what was withheld from the
vendor.

Two independent, composable modes are supported:

* ``strip_samples`` — drop ``sample_documents`` / ``sample_edges`` entirely.
* ``mask_field_values`` — replace concrete *data values* (type-discriminator
  values) with opaque tokens while preserving field names, distinct-value
  counts, and structure. A single snapshot-wide value→token map is used so the
  same value masks to the same token everywhere it appears:
  ``sample_field_value_counts`` values, ``observed_fields.by_type`` keys, and
  ``edge_endpoints.entity_types_by_relation`` (relation keys + resolved
  endpoint entity-type lists). Type values resolved purely from collection names
  (Property-Graph endpoints) are left intact because they are not field data.

Field *name* masking (with output round-tripping) remains future work; it is
intentionally excluded here because masking names without faithfully restoring
them in the LLM output would corrupt the physical mapping.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

REDACTED_VALUE_TOKEN = "<redacted>"

_SAMPLE_KEYS = ("sample_documents", "sample_edges")


@dataclass(frozen=True)
class RedactionOptions:
    strip_samples: bool = False
    mask_field_values: bool = False

    @property
    def active(self) -> bool:
        return self.strip_samples or self.mask_field_values

    @classmethod
    def from_dict(cls, data: Any) -> RedactionOptions:
        if not isinstance(data, dict):
            return cls()
        return cls(
            strip_samples=bool(data.get("stripSamples", False)),
            mask_field_values=bool(data.get("maskFieldValues", False)),
        )


def _collect_sensitive_values(collections: list[Any]) -> dict[str, str]:
    """Build a deterministic value→token map from every data value in the snapshot.

    Sources are the discriminator-value spaces: ``sample_field_value_counts``
    values and ``observed_fields.by_type`` keys. Sorting before assignment keeps
    the mapping stable across runs.
    """
    values: set[str] = set()
    for entry in collections:
        if not isinstance(entry, dict):
            continue
        svc = entry.get("sample_field_value_counts")
        if isinstance(svc, dict):
            for items in svc.values():
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and "value" in item:
                            values.add(str(item["value"]))
        observed = entry.get("observed_fields")
        if isinstance(observed, dict) and isinstance(observed.get("by_type"), dict):
            values.update(str(k) for k in observed["by_type"])
    return {v: f"{REDACTED_VALUE_TOKEN}:{i}" for i, v in enumerate(sorted(values))}


def _mask_value_counts(value_counts: dict[str, Any], token_map: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field, items in value_counts.items():
        if not isinstance(items, list):
            out[field] = items
            continue
        masked: list[Any] = []
        for item in items:
            if isinstance(item, dict) and "value" in item:
                new_item = dict(item)
                new_item["value"] = token_map.get(str(item["value"]), f"{REDACTED_VALUE_TOKEN}")
                masked.append(new_item)
            else:
                masked.append(item)
        out[field] = masked
    return out


def _mask_observed_fields(observed: dict[str, Any], token_map: dict[str, str]) -> None:
    by_type = observed.get("by_type")
    if isinstance(by_type, dict):
        observed["by_type"] = {token_map.get(str(k), str(k)): v for k, v in by_type.items()}


def _mask_edge_endpoints(endpoints: dict[str, Any], token_map: dict[str, str]) -> None:
    by_rel = endpoints.get("entity_types_by_relation")
    if not isinstance(by_rel, dict):
        return
    new_by_rel: dict[str, Any] = {}
    for rel, info in by_rel.items():
        masked_info = info
        if isinstance(info, dict):
            masked_info = dict(info)
            for side in ("from_entity_types", "to_entity_types"):
                vals = masked_info.get(side)
                if isinstance(vals, list):
                    masked_info[side] = [token_map.get(str(v), str(v)) for v in vals]
        new_by_rel[token_map.get(str(rel), str(rel))] = masked_info
    endpoints["entity_types_by_relation"] = new_by_rel


def redact_snapshot_for_egress(snapshot: dict[str, Any], options: RedactionOptions | None) -> dict[str, Any]:
    """Return a redacted deep copy of ``snapshot`` for LLM egress.

    When ``options`` is ``None`` or inactive, returns the snapshot unchanged
    (same object) to preserve byte-identical prompts and avoid needless copies.
    """
    if options is None or not options.active:
        return snapshot

    redacted = copy.deepcopy(snapshot)
    collections = redacted.get("collections")
    if not isinstance(collections, list):
        return redacted

    token_map = _collect_sensitive_values(collections) if options.mask_field_values else {}

    for entry in collections:
        if not isinstance(entry, dict):
            continue
        if options.strip_samples:
            for key in _SAMPLE_KEYS:
                entry.pop(key, None)
        if options.mask_field_values:
            svc = entry.get("sample_field_value_counts")
            if isinstance(svc, dict):
                entry["sample_field_value_counts"] = _mask_value_counts(svc, token_map)
            observed = entry.get("observed_fields")
            if isinstance(observed, dict):
                _mask_observed_fields(observed, token_map)
            endpoints = entry.get("edge_endpoints")
            if isinstance(endpoints, dict):
                _mask_edge_endpoints(endpoints, token_map)
    return redacted
