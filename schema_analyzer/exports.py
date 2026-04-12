from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .types import AnalysisResult


def export_mapping(analysis: AnalysisResult | dict[str, Any], target: Literal["cypher"] = "cypher") -> dict[str, Any]:
    """
    Export the analysis/mapping into a stable JSON contract consumable by transpilers.
    v0.1: target is accepted but only 'cypher' is supported.
    """
    if target != "cypher":
        raise ValueError(f"Unsupported export target: {target}")

    data = analysis.model_dump() if hasattr(analysis, "model_dump") else analysis

    return {
        "conceptualSchema": data.get("conceptual_schema") or data.get("conceptualSchema"),
        "physicalMapping": data.get("physical_mapping") or data.get("physicalMapping"),
        "metadata": data.get("metadata"),
    }
