from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .defaults import DEFAULT_OWL_BASE_IRI, DEFAULT_OWL_PHYSICAL_IRI
from .utils import normalize_analysis_dict

if TYPE_CHECKING:
    from .types import AnalysisResult


def _ttl_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _cardinality_characteristics(pattern: str | None) -> tuple[bool, bool]:
    """Map an observed cardinality pattern to (functional, inverse_functional).

    Functional ⇔ each subject has ≤1 object (low out-degree); inverse-functional
    ⇔ each object has ≤1 subject (low in-degree). Derived from sampled
    statistics, so it is an *observed* characteristic, not a guaranteed axiom.
    """
    return {
        "1:1": (True, True),
        "N:1": (True, False),
        "1:N": (False, True),
        "N:M": (False, False),
    }.get(pattern or "", (False, False))


def _cardinality_by_relationship(data: dict[str, Any]) -> dict[str, str]:
    meta = data.get("metadata")
    stats = meta.get("statistics") if isinstance(meta, dict) else None
    rels = stats.get("relationships") if isinstance(stats, dict) else None
    out: dict[str, str] = {}
    if isinstance(rels, dict):
        for rtype, block in rels.items():
            if isinstance(block, dict) and isinstance(block.get("cardinality_pattern"), str):
                out[rtype] = block["cardinality_pattern"]
    return out


def _subclass_edges(physical_mapping: dict[str, Any]) -> list[tuple[str, str]]:
    """(subclass_entity, family_name) pairs derived from shardFamilies.

    Each member of a structurally-identical shard family is modelled as an
    ``rdfs:subClassOf`` the synthesized family class.
    """
    families = physical_mapping.get("shardFamilies")
    edges: list[tuple[str, str]] = []
    if not isinstance(families, list):
        return edges
    for fam in families:
        if not isinstance(fam, dict):
            continue
        fam_name = fam.get("name")
        members = fam.get("members")
        if not isinstance(fam_name, str) or not fam_name or not isinstance(members, list):
            continue
        for m in members:
            if isinstance(m, dict) and isinstance(m.get("entity"), str) and m["entity"]:
                edges.append((m["entity"], fam_name))
    return edges


def _sanitize_iri_local(name: str) -> str:
    """Sanitize a string for use as a Turtle IRI local name (PN_LOCAL)."""
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized or "_Unknown"


def export_conceptual_model_as_owl_turtle(
    analysis: AnalysisResult | dict[str, Any],
    *,
    base_iri: str = DEFAULT_OWL_BASE_IRI,
    phys_iri: str = DEFAULT_OWL_PHYSICAL_IRI,
) -> str:
    """
    Minimal OWL Turtle export for the conceptual schema + physical mappings.
    This is a normalized conceptual model intended to be used as the basis for:
    - LPG-like conceptual Cypher (labels + rel types)
    - SPARQL conceptual queries
    - physical mappings to guide translation to AQL
    """
    data = normalize_analysis_dict(analysis)

    cs = data.get("conceptualSchema") or {}
    pm = data.get("physicalMapping") or {}

    entities = cs.get("entities") or []
    rels = cs.get("relationships") or []

    lines: list[str] = []
    lines.append(f"@prefix : <{base_iri}> .")
    lines.append("@prefix owl: <http://www.w3.org/2002/07/owl#> .")
    lines.append("@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .")
    lines.append("@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .")
    lines.append("@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .")
    lines.append(f"@prefix phys: <{phys_iri}> .")
    lines.append("")
    lines.append(": a owl:Ontology ;")
    lines.append('  rdfs:label "Conceptual Schema" ;')
    lines.append('  rdfs:comment "Conceptual schema inferred from ArangoDB physical schema." .')
    lines.append("")

    # Declare annotation properties for physical mappings (best-effort; aligns with docs/OWL-FOR-CONCEPTUAL-SCHEMA.md)
    for ap in ["mappingStyle", "collectionName", "typeField", "typeValue", "edgeCollectionName"]:
        lines.append(f"phys:{ap} a owl:AnnotationProperty .")
    lines.append("")

    # Classes
    if isinstance(entities, list):
        for e in entities:
            if not isinstance(e, dict):
                continue
            name = e.get("name")
            if not isinstance(name, str) or not name:
                continue
            safe = _sanitize_iri_local(name)
            iri = f":{safe}"
            lines.append(f"{iri} a owl:Class ;")
            lines.append(f'  rdfs:label "{_ttl_escape(name)}" .')
            mapping = (pm.get("entities") or {}).get(name) if isinstance(pm.get("entities"), dict) else None
            if isinstance(mapping, dict):
                style = mapping.get("style")
                if style:
                    lines.append(f'{iri} phys:mappingStyle "{_ttl_escape(str(style))}" .')
                if mapping.get("collectionName"):
                    lines.append(f'{iri} phys:collectionName "{_ttl_escape(str(mapping["collectionName"]))}" .')
                if mapping.get("typeField"):
                    lines.append(f'{iri} phys:typeField "{_ttl_escape(str(mapping["typeField"]))}" .')
                if mapping.get("typeValue"):
                    lines.append(f'{iri} phys:typeValue "{_ttl_escape(str(mapping["typeValue"]))}" .')
            lines.append("")

    # Class hierarchy from shard families (rdfs:subClassOf)
    subclass_edges = _subclass_edges(pm)
    if subclass_edges:
        family_names = sorted({fam for _, fam in subclass_edges})
        for fam in family_names:
            fam_iri = f":{_sanitize_iri_local(fam)}"
            lines.append(f"{fam_iri} a owl:Class ;")
            lines.append(f'  rdfs:label "{_ttl_escape(fam)}" .')
        for member, fam in sorted(subclass_edges):
            lines.append(f":{_sanitize_iri_local(member)} rdfs:subClassOf :{_sanitize_iri_local(fam)} .")
        lines.append("")

    # Object properties (relationships)
    cardinality_by_rel = _cardinality_by_relationship(data)
    if isinstance(rels, list):
        for r in rels:
            if not isinstance(r, dict):
                continue
            rtype = r.get("type")
            if not isinstance(rtype, str) or not rtype:
                continue
            from_e = r.get("fromEntity")
            to_e = r.get("toEntity")
            safe_rtype = _sanitize_iri_local(rtype)
            iri = f":{safe_rtype}"
            lines.append(f"{iri} a owl:ObjectProperty ;")
            lines.append(f'  rdfs:label "{_ttl_escape(rtype)}" ;')
            if isinstance(from_e, str) and from_e:
                lines.append(f"  rdfs:domain :{_sanitize_iri_local(from_e)} ;")
            if isinstance(to_e, str) and to_e:
                lines.append(f"  rdfs:range :{_sanitize_iri_local(to_e)} ;")
            lines[-1] = lines[-1].rstrip(";") + " ." if lines[-1].endswith(";") else lines[-1]

            mapping = (pm.get("relationships") or {}).get(rtype) if isinstance(pm.get("relationships"), dict) else None
            if isinstance(mapping, dict):
                style = mapping.get("style")
                if style:
                    lines.append(f'{iri} phys:mappingStyle "{_ttl_escape(str(style))}" .')
                if mapping.get("edgeCollectionName"):
                    val = _ttl_escape(str(mapping["edgeCollectionName"]))
                    lines.append(f'{iri} phys:edgeCollectionName "{val}" .')
                if mapping.get("typeField"):
                    lines.append(f'{iri} phys:typeField "{_ttl_escape(str(mapping["typeField"]))}" .')
                if mapping.get("typeValue"):
                    lines.append(f'{iri} phys:typeValue "{_ttl_escape(str(mapping["typeValue"]))}" .')

            pattern = cardinality_by_rel.get(rtype)
            if pattern:
                lines.append(f'{iri} phys:observedCardinality "{_ttl_escape(pattern)}" .')
                functional, inverse_functional = _cardinality_characteristics(pattern)
                if functional:
                    lines.append(f"{iri} a owl:FunctionalProperty .")
                if inverse_functional:
                    lines.append(f"{iri} a owl:InverseFunctionalProperty .")

            inverse_of = r.get("inverseOf")
            if isinstance(inverse_of, str) and inverse_of:
                lines.append(f"{iri} owl:inverseOf :{_sanitize_iri_local(inverse_of)} .")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def export_conceptual_model_as_jsonld(
    analysis: AnalysisResult | dict[str, Any],
    *,
    base_iri: str = DEFAULT_OWL_BASE_IRI,
    phys_iri: str = DEFAULT_OWL_PHYSICAL_IRI,
) -> dict[str, Any]:
    """JSON-LD serialization of the same OWL conceptual model as the Turtle export.

    Carries classes, ``rdfs:subClassOf`` hierarchy (from shard families), object
    properties with domain/range, observed cardinality + functional /
    inverse-functional characteristics (from statistics), explicit
    ``owl:inverseOf``, and the ``phys:`` physical-mapping annotations.
    """
    data = normalize_analysis_dict(analysis)
    cs = data.get("conceptualSchema") or {}
    pm = data.get("physicalMapping") or {}
    raw_entities = cs.get("entities")
    entities = raw_entities if isinstance(raw_entities, list) else []
    raw_rels = cs.get("relationships")
    rels = raw_rels if isinstance(raw_rels, list) else []
    raw_pm_entities = pm.get("entities")
    pm_entities = raw_pm_entities if isinstance(raw_pm_entities, dict) else {}
    raw_pm_rels = pm.get("relationships")
    pm_rels = raw_pm_rels if isinstance(raw_pm_rels, dict) else {}
    cardinality_by_rel = _cardinality_by_relationship(data)

    graph: list[dict[str, Any]] = []
    class_nodes: dict[str, dict[str, Any]] = {}

    for e in entities:
        if not isinstance(e, dict) or not isinstance(e.get("name"), str) or not e["name"]:
            continue
        name = e["name"]
        node: dict[str, Any] = {
            "@id": _sanitize_iri_local(name),
            "@type": "owl:Class",
            "rdfs:label": name,
        }
        mapping = pm_entities.get(name)
        if isinstance(mapping, dict):
            for phys_key, mkey in (
                ("mappingStyle", "style"),
                ("collectionName", "collectionName"),
                ("typeField", "typeField"),
                ("typeValue", "typeValue"),
            ):
                if mapping.get(mkey):
                    node[f"phys:{phys_key}"] = str(mapping[mkey])
        class_nodes[name] = node
        graph.append(node)

    for member, fam in _subclass_edges(pm):
        fam_id = _sanitize_iri_local(fam)
        if fam not in class_nodes:
            fam_node = {"@id": fam_id, "@type": "owl:Class", "rdfs:label": fam}
            class_nodes[fam] = fam_node
            graph.append(fam_node)
        member_node = class_nodes.get(member)
        if member_node is None:
            member_node = {"@id": _sanitize_iri_local(member), "@type": "owl:Class"}
            class_nodes[member] = member_node
            graph.append(member_node)
        member_node["rdfs:subClassOf"] = {"@id": fam_id}

    for r in rels:
        if not isinstance(r, dict) or not isinstance(r.get("type"), str) or not r["type"]:
            continue
        rtype = r["type"]
        types: list[str] = ["owl:ObjectProperty"]
        pattern = cardinality_by_rel.get(rtype)
        functional, inverse_functional = _cardinality_characteristics(pattern)
        if functional:
            types.append("owl:FunctionalProperty")
        if inverse_functional:
            types.append("owl:InverseFunctionalProperty")
        node = {"@id": _sanitize_iri_local(rtype), "@type": types, "rdfs:label": rtype}
        if isinstance(r.get("fromEntity"), str) and r["fromEntity"]:
            node["rdfs:domain"] = {"@id": _sanitize_iri_local(r["fromEntity"])}
        if isinstance(r.get("toEntity"), str) and r["toEntity"]:
            node["rdfs:range"] = {"@id": _sanitize_iri_local(r["toEntity"])}
        if pattern:
            node["phys:observedCardinality"] = pattern
        if isinstance(r.get("inverseOf"), str) and r["inverseOf"]:
            node["owl:inverseOf"] = {"@id": _sanitize_iri_local(r["inverseOf"])}
        mapping = pm_rels.get(rtype)
        if isinstance(mapping, dict):
            if mapping.get("style"):
                node["phys:mappingStyle"] = str(mapping["style"])
            if mapping.get("edgeCollectionName"):
                node["phys:edgeCollectionName"] = str(mapping["edgeCollectionName"])
        graph.append(node)

    return {
        "@context": {
            "@vocab": base_iri,
            "owl": "http://www.w3.org/2002/07/owl#",
            "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "xsd": "http://www.w3.org/2001/XMLSchema#",
            "phys": phys_iri,
        },
        "@graph": graph,
    }
