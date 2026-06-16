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
* ``mask_field_values`` — replace the concrete values in
  ``sample_field_value_counts`` with opaque tokens while preserving the field
  names, distinct-value counts, and frequencies (so type-discriminator shape is
  retained without leaking the actual values).

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


def _mask_value_counts(value_counts: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(value_counts, dict):
        return out
    for field, items in value_counts.items():
        if not isinstance(items, list):
            out[field] = items
            continue
        masked: list[Any] = []
        for i, item in enumerate(items):
            if isinstance(item, dict):
                new_item = dict(item)
                if "value" in new_item:
                    new_item["value"] = f"{REDACTED_VALUE_TOKEN}:{i}"
                masked.append(new_item)
            else:
                masked.append(f"{REDACTED_VALUE_TOKEN}:{i}")
        out[field] = masked
    return out


def redact_snapshot_for_egress(snapshot: dict[str, Any], options: RedactionOptions | None) -> dict[str, Any]:
    """Return a redacted deep copy of ``snapshot`` for LLM egress.

    When ``options`` is ``None`` or inactive, returns the snapshot unchanged
    (same object) to preserve byte-identical prompts and avoid needless copies.
    """
    if options is None or not options.active:
        return snapshot

    redacted = copy.deepcopy(snapshot)
    collections = redacted.get("collections")
    if isinstance(collections, list):
        for entry in collections:
            if not isinstance(entry, dict):
                continue
            if options.strip_samples:
                for key in _SAMPLE_KEYS:
                    entry.pop(key, None)
            if options.mask_field_values and "sample_field_value_counts" in entry:
                entry["sample_field_value_counts"] = _mask_value_counts(entry.get("sample_field_value_counts"))
    return redacted
