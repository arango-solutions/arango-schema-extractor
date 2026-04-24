from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from .utils import normalize_analysis_dict

if TYPE_CHECKING:
    from .types import AnalysisResult


def export_mapping(analysis: AnalysisResult | dict[str, Any], target: Literal["cypher"] = "cypher") -> dict[str, Any]:
    """
    Export the analysis/mapping into a stable JSON contract consumable by transpilers.
    v0.1: target is accepted but only 'cypher' is supported.
    """
    if target != "cypher":
        raise ValueError(f"Unsupported export target: {target}")

    data = normalize_analysis_dict(analysis)

    return {
        "conceptualSchema": data.get("conceptualSchema"),
        "physicalMapping": data.get("physicalMapping"),
        "metadata": data.get("metadata"),
    }
