from schema_analyzer.baseline import infer_baseline_from_snapshot


def test_baseline_creates_entity_per_document_collection_and_rel_per_edge_collection():
    snapshot = {
        "version": 1,
        "generated_at": "x",
        "collections": [
            {"name": "users", "type": "document", "inferred_entity_type": "User"},
            {"name": "follows", "type": "edge", "inferred_relationship_type": "FOLLOWS"},
        ],
        "graphs": [],
    }
    out = infer_baseline_from_snapshot(snapshot)
    assert out["conceptualSchema"]["entities"][0]["name"] == "User"
    assert out["physicalMapping"]["entities"]["User"]["style"] == "COLLECTION"
    assert out["conceptualSchema"]["relationships"][0]["type"] == "FOLLOWS"
    assert out["physicalMapping"]["relationships"]["FOLLOWS"]["style"] == "DEDICATED_COLLECTION"


def test_baseline_uses_label_mapping_when_type_field_has_multiple_values():
    snapshot = {
        "version": 1,
        "collections": [
            {
                "name": "entities",
                "type": "document",
                "candidate_type_fields": ["type"],
                "sample_field_value_counts": {
                    "type": [{"value": "Person", "count": 2}, {"value": "Company", "count": 1}]
                },
            }
        ],
        "graphs": [],
    }
    out = infer_baseline_from_snapshot(snapshot)
    ents = {e["name"] for e in out["conceptualSchema"]["entities"]}
    assert "Person" in ents
    assert "Company" in ents
    assert out["physicalMapping"]["entities"]["Person"]["style"] == "LABEL"
    assert out["physicalMapping"]["entities"]["Person"]["collectionName"] == "entities"


def test_baseline_uses_generic_with_type_for_edge_collections_with_relation_field():
    snapshot = {
        "version": 1,
        "collections": [
            {
                "name": "edges",
                "type": "edge",
                "candidate_type_fields": ["relation"],
                "sample_field_value_counts": {
                    "relation": [{"value": "KNOWS", "count": 2}, {"value": "WORKS_AT", "count": 1}]
                },
            }
        ],
        "graphs": [],
    }
    out = infer_baseline_from_snapshot(snapshot)
    rels = {r["type"] for r in out["conceptualSchema"]["relationships"]}
    assert "KNOWS" in rels
    assert out["physicalMapping"]["relationships"]["KNOWS"]["style"] == "GENERIC_WITH_TYPE"
    assert out["physicalMapping"]["relationships"]["KNOWS"]["collectionName"] == "edges"
