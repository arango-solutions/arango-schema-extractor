from schema_analyzer.eval.generator import PhysicalVariant
from schema_analyzer.eval.scoring import score_domain_range, score_mapping_style


def test_domain_range_scoring_exact_match():
    domain = {
        "entities": [{"name": "User"}, {"name": "Post"}],
        "relationships": [{"type": "AUTHORED", "from": "User", "to": "Post"}],
    }
    predicted = {
        "entities": [{"name": "User"}, {"name": "Post"}],
        "relationships": [{"type": "AUTHORED", "fromEntity": "User", "toEntity": "Post"}],
        "properties": [],
    }
    s = score_domain_range(domain, predicted)
    assert s["f1"] == 1.0
    assert s["pred"] == 1
    assert s["truth"] == 1


def test_domain_range_scoring_wrong_range():
    domain = {
        "entities": [{"name": "User"}, {"name": "Post"}],
        "relationships": [{"type": "AUTHORED", "from": "User", "to": "Post"}],
    }
    predicted = {
        "entities": [{"name": "User"}, {"name": "Post"}],
        "relationships": [{"type": "AUTHORED", "fromEntity": "Post", "toEntity": "User"}],
        "properties": [],
    }
    s = score_domain_range(domain, predicted)
    assert s["f1"] == 0.0


def test_mapping_style_scoring_collection_dedicated():
    domain = {
        "entities": [{"name": "User"}],
        "relationships": [{"type": "FOLLOWS", "from": "User", "to": "User"}],
    }
    variant = PhysicalVariant(name="v", entity_style="COLLECTION", rel_style="DEDICATED_COLLECTION")
    predicted_mapping = {
        "entities": {"User": {"style": "COLLECTION", "collectionName": "users"}},
        "relationships": {"FOLLOWS": {"style": "DEDICATED_COLLECTION", "edgeCollectionName": "follows"}},
    }
    s = score_mapping_style(domain, predicted_mapping, variant)
    assert s["entities"]["accuracy"] == 1.0
    assert s["relationships"]["accuracy"] == 1.0


def test_mapping_style_scoring_generic_generic():
    domain = {
        "entities": [{"name": "User"}],
        "relationships": [{"type": "FOLLOWS", "from": "User", "to": "User"}],
    }
    variant = PhysicalVariant(name="v", entity_style="GENERIC_WITH_TYPE", rel_style="GENERIC_WITH_TYPE")
    predicted_mapping = {
        "entities": {
            "User": {"style": "LABEL", "collectionName": "entities", "typeField": "type", "typeValue": "User"}
        },
        "relationships": {
            "FOLLOWS": {
                "style": "GENERIC_WITH_TYPE",
                "collectionName": "relationships",
                "typeField": "relation",
                "typeValue": "FOLLOWS",
            }
        },
    }
    s = score_mapping_style(domain, predicted_mapping, variant)
    assert s["entities"]["accuracy"] == 1.0
    assert s["relationships"]["accuracy"] == 1.0
