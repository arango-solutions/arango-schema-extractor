"""Conceptual Schema Interchange (CSI) v1 — a direction-agnostic interop format.

CSI is the minimal contract two tools standardize on to exchange a conceptual
model + its ArangoDB physical mapping, independent of *which direction* produced
it:

* ``arango-schema-analyzer`` (this library) emits CSI by reverse-engineering an
  existing ArangoDB graph (``direction = "reverse"``).
* A forward relational→graph tool (e.g. R2G) emits CSI for the graph it built
  (``direction = "forward"``), and can consume an analyzer CSI as a target
  shape.

The document is deliberately the same three blocks this library already
produces — ``conceptualModel`` (≡ ``conceptualSchema``), ``arangoPhysicalMapping``
(≡ ``physicalMapping``) — plus a small ``provenance`` envelope. Detection
enrichments (vci/rdfTopology/graphRag/source/… ) ride along as optional
additive fields; a CSI consumer never requires them.

``to_csi`` / ``from_csi`` are the producer / consumer adapters: ``from_csi``
returns the ``{conceptualSchema, physicalMapping, metadata}`` shape this
library's ``diff_analyses`` / ``compute_gold_comparison`` / quality / export
helpers already accept, so an R2G-produced CSI can be audited here directly.
"""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import Any, cast

from jsonschema import Draft202012Validator

from ..defaults import FALLBACK_LIBRARY_VERSION
from ..utils import normalize_analysis_dict

CSI_VERSION = "1"


def _library_version() -> str:
    try:
        return _pkg_version("arangodb-schema-analyzer")
    except PackageNotFoundError:  # pragma: no cover - source checkout without install
        return FALLBACK_LIBRARY_VERSION


def _meta_get(meta: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if isinstance(meta, dict) and meta.get(k) is not None:
            return meta[k]
    return None


def build_provenance(
    metadata: dict[str, Any] | None,
    *,
    direction: str = "reverse",
    source: dict[str, Any] | None = None,
    producer: str = "arango-schema-analyzer",
    producer_version: str | None = None,
) -> dict[str, Any]:
    """Build the CSI ``provenance`` envelope from analysis metadata.

    ``source`` defaults to an ArangoDB descriptor keyed off the snapshot
    fingerprint already on the metadata; callers (e.g. the tool layer) may pass
    a richer ``{"kind", "ref", "fingerprint"}``.
    """
    meta = metadata if isinstance(metadata, dict) else {}
    fingerprint = _meta_get(meta, "physicalSchemaFingerprint", "physical_schema_fingerprint")
    prov: dict[str, Any] = {
        "producer": producer,
        "producerVersion": producer_version or _library_version(),
        "direction": direction,
        "source": source if isinstance(source, dict) and source else {"kind": "arangodb", "fingerprint": fingerprint},
        "generatedAt": _meta_get(meta, "analysisCompletedAt", "analysis_completed_at", "timestamp"),
    }
    confidence = _meta_get(meta, "confidence")
    if isinstance(confidence, (int, float)):
        prov["confidence"] = confidence
    return prov


def to_csi(
    analysis: Any,
    *,
    direction: str = "reverse",
    source: dict[str, Any] | None = None,
    producer: str = "arango-schema-analyzer",
    producer_version: str | None = None,
) -> dict[str, Any]:
    """Produce a CSI v1 document from an ``AnalysisResult`` or serialized dict."""
    data = normalize_analysis_dict(analysis)
    conceptual = data.get("conceptualSchema")
    physical = data.get("physicalMapping")
    metadata = data.get("metadata")

    return {
        "csiVersion": CSI_VERSION,
        "conceptualModel": conceptual
        if isinstance(conceptual, dict)
        else {"entities": [], "relationships": [], "properties": []},
        "arangoPhysicalMapping": physical if isinstance(physical, dict) else {"entities": {}, "relationships": {}},
        "provenance": build_provenance(
            metadata if isinstance(metadata, dict) else {},
            direction=direction,
            source=source,
            producer=producer,
            producer_version=producer_version,
        ),
    }


def from_csi(csi: dict[str, Any]) -> dict[str, Any]:
    """Adapt a CSI v1 document into the ``{conceptualSchema, physicalMapping,
    metadata}`` shape this library's consumers (diff, gold, quality, exports)
    accept. Provenance is folded into ``metadata`` (confidence / timestamp).
    """
    conceptual = csi.get("conceptualModel")
    physical = csi.get("arangoPhysicalMapping")
    prov = csi.get("provenance")
    prov = prov if isinstance(prov, dict) else {}

    metadata: dict[str, Any] = {}
    if isinstance(prov.get("confidence"), (int, float)):
        metadata["confidence"] = prov["confidence"]
    if isinstance(prov.get("generatedAt"), str):
        metadata["timestamp"] = prov["generatedAt"]
    src = prov.get("source")
    if isinstance(src, dict) and isinstance(src.get("fingerprint"), str):
        metadata["physicalSchemaFingerprint"] = src["fingerprint"]

    return {
        "conceptualSchema": conceptual
        if isinstance(conceptual, dict)
        else {"entities": [], "relationships": [], "properties": []},
        "physicalMapping": physical if isinstance(physical, dict) else {"entities": {}, "relationships": {}},
        "metadata": metadata,
    }


def load_csi_schema_v1() -> dict[str, Any]:
    """Load the bundled CSI v1 JSON Schema."""
    from importlib.resources import files

    p = files("schema_analyzer.csi.v1").joinpath("csi.schema.json")
    return cast("dict[str, Any]", json.loads(p.read_text(encoding="utf-8")))


def validate_csi(document: dict[str, Any]) -> list[str]:
    """Return a sorted list of CSI v1 schema-validation error messages (empty = valid)."""
    validator = Draft202012Validator(load_csi_schema_v1())
    return [err.message for err in sorted(validator.iter_errors(document), key=str)]
