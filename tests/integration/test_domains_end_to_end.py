import pytest

from schema_analyzer import AgenticSchemaAnalyzer
from schema_analyzer.eval import PhysicalVariant, list_domains, load_domain_spec, materialize_domain_variant
from schema_analyzer.snapshot import snapshot_physical_schema

from ..conftest import connect_root, env, skip_if_integration_not_enabled, wait_for_arango

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "variant",
    [
        PhysicalVariant(name="v_collection_dedicated", entity_style="COLLECTION", rel_style="DEDICATED_COLLECTION"),
        PhysicalVariant(name="v_generic_generic", entity_style="GENERIC_WITH_TYPE", rel_style="GENERIC_WITH_TYPE"),
    ],
)
def test_materialize_and_snapshot_domains(variant):
    skip_if_integration_not_enabled()

    client, sys_db = connect_root()
    wait_for_arango(sys_db)
    db_name = env("ARANGO_DB", "schema_analyzer_it")
    # Use a per-variant DB to avoid cross-test interference.
    db_name = f"{db_name}_{variant.name}"
    if sys_db.has_database(db_name):
        import contextlib

        with contextlib.suppress(Exception):
            sys_db.delete_database(db_name)
    sys_db.create_database(db_name)
    db = client.db(db_name, username=env("ARANGO_USER", "root"), password=env("ARANGO_PASS", "openSesame"))

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

