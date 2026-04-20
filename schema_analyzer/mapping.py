from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .errors import SchemaAnalyzerError
from .utils import assert_aql_identifier

EntityMappingStyle = Literal["COLLECTION", "LABEL"]
RelationshipMappingStyle = Literal["DEDICATED_COLLECTION", "GENERIC_WITH_TYPE"]


@dataclass
class PhysicalMapping:
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    relationships: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> PhysicalMapping:
        return cls()

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> PhysicalMapping:
        ent = data.get("entities", {})
        rel = data.get("relationships", {})
        return cls(
            entities=dict(ent) if isinstance(ent, dict) else {},
            relationships=dict(rel) if isinstance(rel, dict) else {},
        )

    def to_json(self) -> dict[str, Any]:
        return {"entities": self.entities, "relationships": self.relationships}

    def get_entity_mapping(self, entity_type: str) -> dict[str, Any] | None:
        return self.entities.get(entity_type)

    def get_relationship_mapping(self, rel_type: str) -> dict[str, Any] | None:
        return self.relationships.get(rel_type)

    def aql_entity_match(self, *, variable: str, entity_type: str) -> dict[str, Any]:
        """
        Injection-safe AQL fragment for matching an entity.
        Returns: {"query": str, "bind_vars": dict}
        """
        assert_aql_identifier("variable", variable)
        if not isinstance(entity_type, str) or not entity_type:
            raise SchemaAnalyzerError("Invalid entity_type", code="INVALID_ARGUMENT")

        mapping = self.get_entity_mapping(entity_type)
        if not mapping:
            raise SchemaAnalyzerError(f"No entity mapping for: {entity_type}", code="MAPPING_NOT_FOUND")

        style = mapping.get("style")
        bind_vars: dict[str, Any] = {}

        if style == "COLLECTION":
            collection_name = mapping.get("collectionName")
            if not collection_name:
                raise SchemaAnalyzerError(
                    f"COLLECTION mapping missing collectionName for: {entity_type}", code="INVALID_MAPPING"
                )
            bind_vars["@collection"] = collection_name
            return {"query": f"FOR {variable} IN @@collection", "bind_vars": bind_vars}

        if style == "LABEL":
            collection_name = mapping.get("collectionName")
            type_field = mapping.get("typeField")
            type_value = mapping.get("typeValue")
            if not (collection_name and type_field and type_value):
                raise SchemaAnalyzerError(
                    f"LABEL mapping requires collectionName, typeField, typeValue for: {entity_type}",
                    code="INVALID_MAPPING",
                )
            bind_vars["@collection"] = collection_name
            bind_vars["typeField"] = type_field
            bind_vars["typeValue"] = type_value
            return {
                "query": f"FOR {variable} IN @@collection FILTER {variable}[@typeField] == @typeValue",
                "bind_vars": bind_vars,
            }

        raise SchemaAnalyzerError(f"Unsupported entity mapping style: {style}", code="INVALID_MAPPING")

    def aql_relationship_traversal(
        self,
        *,
        from_variable: str,
        rel_type: str,
        to_variable: str,
        edge_variable: str = "e",
        direction: Literal["outbound", "inbound"] = "outbound",
    ) -> dict[str, Any]:
        """
        Minimal AQL fragment to traverse an edge collection and load the other endpoint with DOCUMENT().
        Returns: {"query": str, "bind_vars": dict, "edge_variable": str}
        """
        assert_aql_identifier("from_variable", from_variable)
        assert_aql_identifier("to_variable", to_variable)
        assert_aql_identifier("edge_variable", edge_variable)
        if not isinstance(rel_type, str) or not rel_type:
            raise SchemaAnalyzerError("Invalid rel_type", code="INVALID_ARGUMENT")
        if direction not in ("outbound", "inbound"):
            raise SchemaAnalyzerError(f"Invalid direction: {direction}", code="INVALID_ARGUMENT")

        mapping = self.get_relationship_mapping(rel_type)
        if not mapping:
            raise SchemaAnalyzerError(f"No relationship mapping for: {rel_type}", code="MAPPING_NOT_FOUND")

        from_field = "_from" if direction == "outbound" else "_to"
        to_field = "_to" if direction == "outbound" else "_from"

        bind_vars: dict[str, Any] = {}
        style = mapping.get("style")

        if style == "DEDICATED_COLLECTION":
            edge_collection_name = mapping.get("edgeCollectionName")
            if not edge_collection_name:
                raise SchemaAnalyzerError(
                    f"DEDICATED_COLLECTION mapping missing edgeCollectionName for: {rel_type}", code="INVALID_MAPPING"
                )
            bind_vars["@edgeCollection"] = edge_collection_name
            query = "\n".join(
                [
                    f"FOR {edge_variable} IN @@edgeCollection",
                    f"  FILTER {edge_variable}.{from_field} == {from_variable}._id",
                    f"  LET {to_variable} = DOCUMENT({edge_variable}.{to_field})",
                ]
            )
            return {"edge_variable": edge_variable, "bind_vars": bind_vars, "query": query}

        if style == "GENERIC_WITH_TYPE":
            edge_collection_name = mapping.get("edgeCollectionName")
            type_field = mapping.get("typeField")
            type_value = mapping.get("typeValue")
            if not (edge_collection_name and type_field and type_value):
                raise SchemaAnalyzerError(
                    f"GENERIC_WITH_TYPE mapping requires edgeCollectionName, typeField, typeValue for: {rel_type}",
                    code="INVALID_MAPPING",
                )
            bind_vars["@edgeCollection"] = edge_collection_name
            bind_vars["typeField"] = type_field
            bind_vars["typeValue"] = type_value
            query = "\n".join(
                [
                    f"FOR {edge_variable} IN @@edgeCollection",
                    f"  FILTER {edge_variable}.{from_field} == {from_variable}._id",
                    f"  FILTER {edge_variable}[@typeField] == @typeValue",
                    f"  LET {to_variable} = DOCUMENT({edge_variable}.{to_field})",
                ]
            )
            return {"edge_variable": edge_variable, "bind_vars": bind_vars, "query": query}

        raise SchemaAnalyzerError(f"Unsupported relationship mapping style: {style}", code="INVALID_MAPPING")
