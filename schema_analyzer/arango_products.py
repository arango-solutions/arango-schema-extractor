"""Detect first-party ArangoDB product artefacts in a snapshot.

This module recognizes when a database contains graphs created by
official Arango products so downstream consumers (the agentic UI,
report generators, GraphSet workbenches) can:

* Badge the graphs with the right purpose (corpus / knowledge_graph)
* Auto-pair related graphs (an Autograph project's CorpusGraph and
  its companion ``_kg``) into a single GraphSet
* Surface partial / failed product runs as actionable warnings
* Inject the right semantic priors when generating use cases or
  AQL templates against these graphs

The only product detected today is **Autograph** (Arango's
text-to-graph + GraphRAG import pipeline). The result shape is
deliberately product-agnostic - adding a future detector
(``detect_arango_search``, etc.) means writing a peer function and
merging its output into the same ``ArangoProductReport``.

The result is published on :class:`AnalysisMetadata.arango_product`
and the status string on ``arango_product_status``, mirroring the
sharding-profile / multitenancy / domain-detect pattern.

Detection is purely structural - collection naming patterns +
named-graph definitions. No samples, no LLM, no DB I/O. Safe to
call from the synchronous preparation pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Autograph naming convention (source: autograph/corpus_graph/naming.py).
# Collections are prefixed with ``<project>_`` at project-creation time;
# the unprefixed suffixes below are the canonical Autograph artefacts.
# Corpus side comes from autograph/corpus_graph; KG side from the
# GraphRAG importer.

# (suffix, kind, role) ordered LONGEST FIRST so multi-word suffixes
# win over single-word ones (``corpus_relations`` must match before
# the bare ``relations`` candidate).
_AUTOGRAPH_SUFFIX_RULES: tuple[tuple[str, str, str], ...] = (
    ("corpus_relations", "edge", "corpus"),
    ("similarities", "edge", "corpus"),
    ("Relations", "edge", "kg"),
    ("CorpusGraph", "graph", "corpus"),
    ("kg", "graph", "kg"),
    ("Chunks", "vertex", "kg"),
    ("Communities", "vertex", "kg"),
    ("Documents", "vertex", "kg"),
    ("Entities", "vertex", "kg"),
    ("domains", "vertex", "corpus"),
    ("modules", "vertex", "corpus"),
    ("sources", "vertex", "corpus"),
    ("rags", "vertex", "corpus"),
)

_CORPUS_VERTEX_SUFFIXES = frozenset({"domains", "modules", "sources", "rags"})
_KG_VERTEX_SUFFIXES = frozenset({"Chunks", "Communities", "Documents", "Entities"})

# Strong-marker gate: at least one of these suffixes must be present
# before we report ``kind=autograph``. Avoids false-positives when a
# hand-built KG happens to use a generic name like ``Documents``.
_STRONG_AUTOGRAPH_MARKERS = frozenset(
    {"CorpusGraph", "kg", "corpus_relations", "rags"}
)

# Implicit cross-graph link: GraphRAG seeds the KG's
# ``Entities.entity_type`` values from the corpus's ``rags.entity_types``.
_AUTOGRAPH_IMPLICIT_LINKS: tuple[tuple[str, str, str, str], ...] = (
    ("rags", "entity_types", "Entities", "entity_type"),
)


@dataclass(frozen=True)
class AutographProject:
    """One Autograph project detected inside a database."""

    project_name: str
    completeness: str  # "complete" | "corpus_only" | "kg_only"
    corpus_graph: str | None = None
    kg_graph: str | None = None
    corpus_collections: dict[str, str] = field(default_factory=dict)
    kg_collections: dict[str, str] = field(default_factory=dict)
    implicit_links: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "completeness": self.completeness,
            "corpus_graph": self.corpus_graph,
            "kg_graph": self.kg_graph,
            "corpus_collections": dict(self.corpus_collections),
            "kg_collections": dict(self.kg_collections),
            "implicit_links": list(self.implicit_links),
            "warnings": list(self.warnings),
            "confidence": round(self.confidence, 2),
        }


@dataclass(frozen=True)
class ArangoProductReport:
    """Aggregate report - one per snapshot."""

    autograph_projects: list[AutographProject] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.autograph_projects

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "autograph" if self.autograph_projects else None,
            "version_hint": "graphrag" if self.autograph_projects else None,
            "projects": [p.to_dict() for p in self.autograph_projects],
        }


def detect_arango_products(snapshot: dict[str, Any]) -> ArangoProductReport:
    """Inspect a physical snapshot and return any first-party Arango
    product artefacts found."""
    return _detect_autograph(snapshot)


def _detect_autograph(snapshot: dict[str, Any]) -> ArangoProductReport:
    collection_names = _collect_names(snapshot, "collections")
    graph_names = _collect_graph_names(snapshot)

    by_project: dict[str, dict[str, Any]] = {}

    for name in collection_names:
        prefix, suffix, kind, _role = _match_suffix(name)
        if prefix is None or suffix is None or kind == "graph":
            continue
        bucket = by_project.setdefault(
            prefix, {"vertex": {}, "edge": {}, "graphs": {}}
        )
        bucket["edge" if kind == "edge" else "vertex"][suffix] = name

    for name in graph_names:
        prefix, suffix, kind, _role = _match_suffix(name)
        if prefix is None or suffix is None or kind != "graph":
            continue
        bucket = by_project.setdefault(
            prefix, {"vertex": {}, "edge": {}, "graphs": {}}
        )
        bucket["graphs"][suffix] = name

    projects: list[AutographProject] = []
    for prefix, bucket in sorted(by_project.items()):
        all_suffixes = (
            set(bucket["vertex"].keys())
            | set(bucket["edge"].keys())
            | set(bucket["graphs"].keys())
        )
        if not (all_suffixes & _STRONG_AUTOGRAPH_MARKERS):
            continue
        projects.append(_classify_project(prefix, bucket))

    if projects:
        logger.info(
            "Detected %d Autograph project(s): %s",
            len(projects),
            [p.project_name for p in projects],
        )

    return ArangoProductReport(autograph_projects=projects)


def _classify_project(
    prefix: str, bucket: dict[str, Any]
) -> AutographProject:
    vertex_suffixes = set(bucket["vertex"].keys())
    edge_suffixes = set(bucket["edge"].keys())
    graph_suffixes = set(bucket["graphs"].keys())

    has_corpus_evidence = bool(
        (vertex_suffixes & _CORPUS_VERTEX_SUFFIXES)
        or "CorpusGraph" in graph_suffixes
        or "corpus_relations" in edge_suffixes
        or "similarities" in edge_suffixes
    )
    has_kg_evidence = bool(
        (vertex_suffixes & _KG_VERTEX_SUFFIXES)
        or "kg" in graph_suffixes
        or "Relations" in edge_suffixes
    )

    if has_corpus_evidence and has_kg_evidence:
        completeness = "complete"
    elif has_corpus_evidence:
        completeness = "corpus_only"
    elif has_kg_evidence:
        completeness = "kg_only"
    else:  # pragma: no cover
        completeness = "unknown"

    corpus_collections: dict[str, str] = {}
    kg_collections: dict[str, str] = {}
    for suffix, name in bucket["vertex"].items():
        if suffix in _CORPUS_VERTEX_SUFFIXES:
            corpus_collections[suffix] = name
        elif suffix in _KG_VERTEX_SUFFIXES:
            kg_collections[suffix] = name
    for suffix, name in bucket["edge"].items():
        if suffix in {"corpus_relations", "similarities"}:
            corpus_collections[suffix] = name
        elif suffix == "Relations":
            kg_collections[suffix] = name

    implicit_links: list[dict[str, str]] = []
    for from_suffix, from_field, to_suffix, to_field in _AUTOGRAPH_IMPLICIT_LINKS:
        from_name = corpus_collections.get(from_suffix)
        to_name = kg_collections.get(to_suffix)
        if from_name and to_name:
            implicit_links.append(
                {
                    "from": f"{from_name}.{from_field}",
                    "to": f"{to_name}.{to_field}",
                    "kind": "graphrag_entity_type_seed",
                }
            )

    corpus_score = (
        len(vertex_suffixes & _CORPUS_VERTEX_SUFFIXES)
        / len(_CORPUS_VERTEX_SUFFIXES)
    )
    kg_score = (
        len(vertex_suffixes & _KG_VERTEX_SUFFIXES) / len(_KG_VERTEX_SUFFIXES)
    )
    base = max(corpus_score, kg_score)
    bonus = 0.0
    if "CorpusGraph" in graph_suffixes or "kg" in graph_suffixes:
        bonus += 0.1
    if "corpus_relations" in edge_suffixes or "Relations" in edge_suffixes:
        bonus += 0.05
    confidence = min(1.0, base + bonus)

    warnings: list[str] = []
    if completeness == "corpus_only":
        warnings.append(
            "INCOMPLETE_AUTOGRAPH_RUN: corpus exists but no companion "
            "knowledge graph found; the GraphRAG import phase may have "
            "failed or not yet been run."
        )
    elif completeness == "kg_only":
        warnings.append(
            "ORPHAN_AUTOGRAPH_KG: knowledge graph exists but no corpus "
            "found; the corpus may have been deleted independently."
        )

    return AutographProject(
        project_name=prefix,
        completeness=completeness,
        corpus_graph=bucket["graphs"].get("CorpusGraph"),
        kg_graph=bucket["graphs"].get("kg"),
        corpus_collections=corpus_collections,
        kg_collections=kg_collections,
        implicit_links=implicit_links,
        warnings=warnings,
        confidence=confidence,
    )


def _collect_names(snapshot: dict[str, Any], key: str) -> list[str]:
    out: list[str] = []
    for entry in snapshot.get(key) or []:
        if isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str) and name:
                out.append(name)
    return out


def _collect_graph_names(snapshot: dict[str, Any]) -> list[str]:
    """Pull graph names from both ``graphs`` and ``graphs_detailed``
    keys (snapshots in the wild use either)."""
    seen: set[str] = set()
    out: list[str] = []
    for key in ("graphs_detailed", "graphs"):
        for name in _collect_names(snapshot, key):
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


def _match_suffix(
    name: str,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Greedy longest-suffix match.

    Returns ``(project_prefix, suffix, kind, role)`` if ``name`` ends
    with ``_<suffix>`` for one of the rules; ``(None, None, None, None)``
    otherwise. Greedy is critical so
    ``OpenRTB-API-Specification_corpus_relations`` matches suffix
    ``corpus_relations``, not ``relations``.
    """
    if "_" not in name:
        return None, None, None, None
    for suffix, kind, role in _AUTOGRAPH_SUFFIX_RULES:
        anchor = "_" + suffix
        if name.endswith(anchor) and len(name) > len(anchor):
            prefix = name[: -len(anchor)]
            return prefix, suffix, kind, role
    return None, None, None, None
