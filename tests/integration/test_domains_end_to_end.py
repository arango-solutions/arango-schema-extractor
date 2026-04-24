import pytest

from schema_analyzer import AgenticSchemaAnalyzer
from schema_analyzer.eval import PhysicalVariant, list_domains, load_domain_spec, materialize_domain_variant
from schema_analyzer.snapshot import snapshot_physical_schema

from ..conftest import env

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "variant",
    [
        PhysicalVariant(name="v_collection_dedicated", entity_style="COLLECTION", rel_style="DEDICATED_COLLECTION"),
        PhysicalVariant(name="v_generic_generic", entity_style="GENERIC_WITH_TYPE", rel_style="GENERIC_WITH_TYPE"),
    ],
)
def test_materialize_and_snapshot_domains(variant, fresh_database):
    base_db = env("ARANGO_DB", "schema_analyzer_it")
    db = fresh_database(f"{base_db}_{variant.name}")

    domains = list_domains()
    assert domains, "no domain specs found"

    for d in domains:
        spec = load_domain_spec(d)
        materialize_domain_variant(db, spec, variant, seed=1, scale=3, create_graph=True)

    snap = snapshot_physical_schema(db, sample_limit_per_collection=1, include_samples_in_snapshot=False)
    assert snap["collections"], "snapshot should include collections"
    # At least one graph attempt should show up (best-effort).
    assert "graphs" in snap

    # Analyzer runs even without provider (graceful degradation).
    analyzer = AgenticSchemaAnalyzer(llm_provider=None, api_key=None)
    analysis = analyzer.analyze_physical_schema(db, sample_limit_per_collection=1)
    assert analysis.metadata.review_required is True
