"""Element-level source provenance (PRD §3.13.2).

Stamps every conceptual entity/relationship and physical-mapping entry with a
``source`` tag so downstream auditors can tell where each element came from:

* ``"llm"`` — produced by the LLM generate/validate/repair loop.
* ``"baseline"`` — produced by deterministic inference, either because no LLM
  was configured or because the reconciliation step backfilled a collection the
  LLM omitted (those specific elements are baseline-derived even on an LLM run).
* ``"human"`` — preserved verbatim if an element already carries
  ``source: "human"`` (e.g. a curated mapping fed back in for re-analysis).

The annotator is deterministic and additive; it never overwrites an existing
``"human"`` tag and never changes any other field.
"""

from __future__ import annotations

from typing import Any

SOURCE_LLM = "llm"
SOURCE_BASELINE = "baseline"
SOURCE_HUMAN = "human"


def _backfilled_collections(data: dict[str, Any]) -> set[str]:
    meta = data.get("metadata")
    if not isinstance(meta, dict):
        return set()
    recon = meta.get("reconciliation")
    if not isinstance(recon, dict):
        return set()
    cols = recon.get("backfilled_collections")
    return {c for c in cols if isinstance(c, str)} if isinstance(cols, list) else set()


def _tag(entry: dict[str, Any], source: str) -> None:
    if entry.get("source") == SOURCE_HUMAN:
        return
    entry["source"] = source


def annotate_provenance(data: dict[str, Any], *, used_baseline: bool) -> None:
    """Annotate ``data`` (a mutable analysis dict) with per-element ``source``.

    ``data`` must have the ``{conceptualSchema, physicalMapping, metadata}``
    shape. Mutates in place. The default source is ``baseline`` when
    ``used_baseline`` is true, otherwise ``llm``; physical-mapping entries whose
    collection was backfilled by reconciliation are always tagged ``baseline``.
    """
    default_source = SOURCE_BASELINE if used_baseline else SOURCE_LLM
    backfilled = _backfilled_collections(data)

    cs = data.get("conceptualSchema")
    pm = data.get("physicalMapping")
    pm = pm if isinstance(pm, dict) else {}

    raw_pm_entities = pm.get("entities")
    pm_entities: dict[str, Any] = raw_pm_entities if isinstance(raw_pm_entities, dict) else {}
    raw_pm_rels = pm.get("relationships")
    pm_rels: dict[str, Any] = raw_pm_rels if isinstance(raw_pm_rels, dict) else {}

    # Physical mapping entries — tag baseline when their collection was backfilled.
    for entry in pm_entities.values():
        if not isinstance(entry, dict):
            continue
        col = entry.get("collectionName")
        source = SOURCE_BASELINE if isinstance(col, str) and col in backfilled else default_source
        _tag(entry, source)

    for entry in pm_rels.values():
        if not isinstance(entry, dict):
            continue
        col = entry.get("edgeCollectionName") or entry.get("collectionName")
        source = SOURCE_BASELINE if isinstance(col, str) and col in backfilled else default_source
        _tag(entry, source)

    # Conceptual elements inherit the source of their physical mapping entry
    # when one exists (so a backfilled entity reads "baseline"), else default.
    if isinstance(cs, dict):
        for e in cs.get("entities", []) or []:
            if not isinstance(e, dict) or not isinstance(e.get("name"), str):
                continue
            mapped = pm_entities.get(e["name"])
            inherited = mapped.get("source") if isinstance(mapped, dict) else None
            _tag(e, inherited if isinstance(inherited, str) else default_source)

        for r in cs.get("relationships", []) or []:
            if not isinstance(r, dict) or not isinstance(r.get("type"), str):
                continue
            mapped = pm_rels.get(r["type"])
            inherited = mapped.get("source") if isinstance(mapped, dict) else None
            _tag(r, inherited if isinstance(inherited, str) else default_source)
