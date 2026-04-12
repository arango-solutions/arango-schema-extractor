from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .types import AnalysisResult


def generate_schema_docs(analysis: AnalysisResult | dict[str, Any]) -> str:
    """
    Generate human-readable Markdown documentation from an analysis result.
    Accepts either AnalysisResult (pydantic) or a plain dict with the same keys.
    """
    data = analysis.model_dump() if hasattr(analysis, "model_dump") else analysis

    cs = data.get("conceptual_schema") or data.get("conceptualSchema") or {}
    pm = data.get("physical_mapping") or data.get("physicalMapping") or {}
    md = data.get("metadata") or {}

    entities = cs.get("entities", []) or []
    rels = cs.get("relationships", []) or []

    lines = []
    lines.append("## Schema Analysis")
    lines.append("")
    lines.append(f"- **Confidence**: {md.get('confidence')}")
    lines.append(f"- **Timestamp**: {md.get('timestamp')}")
    lines.append("")

    lines.append("### Conceptual schema")
    lines.append("")
    lines.append(f"- **Entities**: {len(entities)}")
    lines.append(f"- **Relationships**: {len(rels)}")
    lines.append("")

    lines.append("#### Entities")
    for e in entities:
        if not isinstance(e, dict):
            continue
        name = e.get("name") or "(unnamed)"
        labels = e.get("labels") or []
        lines.append(f"- **{name}**: labels={labels}")

    lines.append("")
    lines.append("#### Relationships")
    for r in rels:
        if not isinstance(r, dict):
            continue
        rtype = r.get("type") or "(untyped)"
        frm = r.get("fromEntity") or r.get("from") or "?"
        to = r.get("toEntity") or r.get("to") or "?"
        lines.append(f"- **{rtype}**: {frm} -> {to}")

    lines.append("")
    lines.append("### Physical mapping (summary)")
    lines.append("")
    lines.append(f"- **Entity mappings**: {len(pm.get('entities') or {})}")
    lines.append(f"- **Relationship mappings**: {len(pm.get('relationships') or {})}")

    return "\n".join(lines) + "\n"
