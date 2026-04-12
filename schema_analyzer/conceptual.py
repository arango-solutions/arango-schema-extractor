from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConceptualSchema:
    entities: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    properties: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def empty(cls) -> ConceptualSchema:
        return cls()

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ConceptualSchema:
        return cls(
            entities=list(data.get("entities", [])) if isinstance(data.get("entities", []), list) else [],
            relationships=list(data.get("relationships", []))
            if isinstance(data.get("relationships", []), list)
            else [],
            properties=list(data.get("properties", [])) if isinstance(data.get("properties", []), list) else [],
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "entities": self.entities,
            "relationships": self.relationships,
            "properties": self.properties,
        }

    def get_entity_by_label(self, label: str) -> dict[str, Any] | None:
        for e in self.entities:
            labels = e.get("labels", []) if isinstance(e, dict) else []
            if isinstance(labels, list) and label in labels:
                return e
        return None

    def has_relationship_type(self, rel_type: str) -> bool:
        return any(isinstance(r, dict) and r.get("type") == rel_type for r in self.relationships)

    def validate_pattern(self, pattern_ast: dict[str, Any]) -> dict[str, Any]:
        """
        Minimal deterministic validation contract for transpilers.

        v0.1 pattern_ast shape:
        {
          "nodes": [{"variable": "u", "labels": ["User"], "properties": ["email"]}],
          "relationships": [{"variable": "r", "type": "FOLLOWS", "properties": ["since"]}]
        }
        """
        errors: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []

        if not isinstance(pattern_ast, dict):
            return {
                "valid": False,
                "errors": [{"code": "INVALID_PATTERN", "message": "pattern_ast must be an object"}],
                "warnings": warnings,
            }

        nodes = pattern_ast.get("nodes", [])
        rels = pattern_ast.get("relationships", [])
        nodes = nodes if isinstance(nodes, list) else []
        rels = rels if isinstance(rels, list) else []

        for node in nodes:
            if not isinstance(node, dict):
                continue
            labels = node.get("labels", [])
            labels = labels if isinstance(labels, list) else []
            for label in labels:
                if not isinstance(label, str) or not label:
                    continue
                known = self.get_entity_by_label(label) is not None or any(
                    isinstance(e, dict) and e.get("name") == label for e in self.entities
                )
                if not known:
                    errors.append({"code": "UNKNOWN_LABEL", "message": f"Unknown label/entity: {label}"})

        for rel in rels:
            if not isinstance(rel, dict):
                continue
            rel_type = rel.get("type")
            if isinstance(rel_type, str) and rel_type.strip():
                if not self.has_relationship_type(rel_type):
                    errors.append({"code": "UNKNOWN_REL_TYPE", "message": f"Unknown relationship type: {rel_type}"})
            else:
                warnings.append({"code": "MISSING_REL_TYPE", "message": "Relationship type not specified in pattern"})

        return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}
