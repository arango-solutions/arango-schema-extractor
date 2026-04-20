import os
import sys

from arango import ArangoClient

from schema_analyzer import AgenticSchemaAnalyzer
from schema_analyzer.eval import PhysicalVariant, list_domains, load_domain_spec, materialize_domain_variant
from schema_analyzer.eval.scoring import score_against_domain, score_domain_range, score_mapping_style


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def connect_db():
    url = env("ARANGO_URL", "http://localhost:8529")
    user = env("ARANGO_USER", "root")
    pw = env("ARANGO_PASS", "openSesame")
    db_name = env("ARANGO_DB", "schema_analyzer_eval")
    client = ArangoClient(hosts=url)
    sys_db = client.db("_system", username=user, password=pw)
    if sys_db.has_database(db_name):
        sys_db.delete_database(db_name, ignore_missing=True)
    sys_db.create_database(db_name)
    return client.db(db_name, username=user, password=pw)


def main():
    provider = env("LLM_PROVIDER", "openrouter")
    model = env("LLM_MODEL", None)

    db = connect_db()
    domains = list_domains()
    if not domains:
        print("No domains found under ./domains", file=sys.stderr)
        return 2

    variants = [
        PhysicalVariant(name="collection_dedicated", entity_style="COLLECTION", rel_style="DEDICATED_COLLECTION"),
        PhysicalVariant(name="generic_generic", entity_style="GENERIC_WITH_TYPE", rel_style="GENERIC_WITH_TYPE"),
    ]

    analyzer = AgenticSchemaAnalyzer(llm_provider=provider, api_key=None, model=model)

    results = []
    for v in variants:
        for d in domains:
            spec = load_domain_spec(d)
            materialize_domain_variant(db, spec, v, seed=1, scale=5, create_graph=True)
            analysis = analyzer.analyze_physical_schema(db, sample_limit_per_collection=3, timeout_ms=60_000)
            score = score_against_domain(spec, analysis.conceptual_schema)
            domain_range = score_domain_range(spec, analysis.conceptual_schema)
            mapping_style = score_mapping_style(spec, analysis.physical_mapping, v)
            results.append(
                {
                    "domain": d,
                    "variant": v.name,
                    "provider": provider,
                    "model": analysis.metadata.model_dump().get("model", None),
                    "confidence": analysis.metadata.confidence,
                    "review_required": analysis.metadata.review_required,
                    "score": score,
                    "domain_range": domain_range,
                    "mapping_style": mapping_style,
                }
            )
            print(
                f"{d:28} {v.name:18} ent_f1={score['entities']['f1']:.2f} "
                f"rel_f1={score['relationships']['f1']:.2f} dr_f1={domain_range['f1']:.2f} "
                f"map_rel_acc={mapping_style['relationships']['accuracy']:.2f} conf={analysis.metadata.confidence:.2f}"
            )

    # Save JSON report
    out_path = env("EVAL_REPORT", "eval_report.json")
    import json

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, sort_keys=True)
    print(f"\nWrote report to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
