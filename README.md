# arangodb-schema-analyzer

Standalone Python library that analyzes an ArangoDB database's physical schema and produces:

- a **conceptual schema** (entities, relationships, properties)
- a **conceptual→physical mapping** suitable for transpilers (Cypher, SPARQL, future)
- **metadata** (confidence, timestamp, analyzed collection counts, detected patterns,
  per-entity tenant scope, deployment-style sharding profile)

Current release: see [`CHANGELOG.md`](CHANGELOG.md). The version is the single
source of truth in [`pyproject.toml`](pyproject.toml).

## Install

From source (this repo):

```bash
python -m pip install -e .
```

Optional LLM provider extras:

```bash
python -m pip install -e ".[openai]"
python -m pip install -e ".[anthropic]"
```

OpenRouter is also supported and requires no extra SDK (uses stdlib `urllib`).

**MCP (Model Context Protocol)** — optional stdio server wrapping the v1 JSON tool contract:

```bash
python -m pip install -e ".[mcp]"
arangodb-schema-analyzer-mcp
```

If you don't install a provider SDK (or you don't provide an API key), analysis degrades gracefully to deterministic baseline inference.

## Usage

```python
from arango import ArangoClient

from schema_analyzer import AgenticSchemaAnalyzer

client = ArangoClient(hosts="http://localhost:8529")
db = client.db("mydb", username="root", password="openSesame")

analyzer = AgenticSchemaAnalyzer(
    llm_provider="openai",  # or "anthropic" or "openrouter"
    api_key=None,           # e.g. os.environ["OPENAI_API_KEY"]
    model="gpt-4o-mini",
    cache={"type": "filesystem", "directory": ".schema-analyzer-cache"},
)

analysis = analyzer.analyze_physical_schema(
    db,
    timeout_ms=60_000,
    sample_limit_per_collection=5,
)

print(analysis.metadata.confidence)
```

## Tool usage (CLI)

This project can be called as a **non-interactive tool** (stdin JSON → stdout JSON) using the v1 contract under `docs/tool-contract/v1/`.

Install (editable):

```bash
python -m pip install -e .
```

Example (analyze) using the provided request example:

```bash
cat docs/tool-contract/v1/examples/request.analyze.json | arangodb-schema-analyzer --pretty
```

### CLI options

```
arangodb-schema-analyzer [--request FILE] [--out FILE] [--pretty] [-v]
```

- `--request FILE` — path to request JSON (default: read from stdin)
- `--out FILE` — write response JSON to file (default: stdout)
- `--pretty` — pretty-print JSON output
- `-v` — enable verbose logging

## Evaluation CLI

Run analysis quality benchmarks against domain packs:

```bash
arangodb-schema-analyzer eval \
  --provider openai \
  --model gpt-4o-mini \
  --report eval_report.json
```

Pass `--baseline <prior-report.json>` to diff a new run against an earlier
report (the baseline file is whatever a previous `--report` produced; no
baseline ships in the repo).

Options: `--url`, `--user`, `--password`, `--database`, `--domains`, `--sample-limit`, `--timeout-ms`, `--scale`, `--no-cleanup`.

Domains included: `healthcare`, `financial_fraud_detection`, `insurance`, `intelligence`, `network_asset_management`.

## Public API

Exports (see `schema_analyzer/__init__.py`):

- `AgenticSchemaAnalyzer` — main analyzer class
- `ConceptualSchema` — conceptual schema dataclass
- `PhysicalMapping` — physical mapping dataclass with AQL helpers
- `generate_schema_docs(analysis)` — Markdown documentation generator
- `export_mapping(analysis, target)` — transpiler export (currently only `cypher`)
- `export_conceptual_model_as_owl_turtle(analysis)` — OWL Turtle export
- `register_provider(name, ...)` — register custom LLM providers
- `list_providers()` — list registered LLM provider names
- `run_tool(request_dict)` — programmatic entrypoint to the v1 tool contract
- `fingerprint_physical_schema(snapshot)` — full-snapshot SHA-256 (cache key)
- `fingerprint_physical_shape(db, *, exclude_collections=None)` — cheap probe
  that hashes only the collection set + per-collection type + index digests
- `fingerprint_physical_counts(db, *, exclude_collections=None)` — shape
  fingerprint combined with per-collection `count()`

## Recent additions

See [`CHANGELOG.md`](CHANGELOG.md) for the full history. Highlights since 0.3.0:

- **0.6.0 — Shard-family detection** (`physicalMapping.shardFamilies`)
  groups conceptual entities that share an identical property set and a
  common name suffix (the per-source / per-repo collection-duplication
  pattern), so downstream consumers can emit UNION-aware guidance
  instead of silently picking one member. Plus **multitenancy classification**
  (`metadata.multitenancy`) layered on the sharding profile.
- **0.5.0 — Sharding-profile classification.** Every analysis stamps
  `metadata.shardingProfile` with one of `OneShard`, `DisjointSmartGraph`,
  `SmartGraph`, `SatelliteGraph`, or `Sharded`, plus per-graph and
  per-collection evidence. Snapshot-only, no extra DB round trip.
- **0.4.0 — Tenant-scope annotations.** Every entity in
  `physicalMapping.entities[*]` now carries a `tenantScope` block
  (`tenant_root` / `tenant_scoped` / `global`), with a per-run
  `metadata.tenantScopeReport` summary. Configurable via
  `SCHEMA_ANALYZER_TENANT_*` env vars.
- **0.3.0 — Cheap change-detection probes** (`fingerprint_physical_shape`,
  `fingerprint_physical_counts`), **statistics block** on
  `metadata.statistics`, and a **reconciliation step** that backfills any
  collections the LLM omitted.

## Configuration

Tunable defaults live in `schema_analyzer/defaults.py` (full list there).
Selected parameters:

| Parameter | Default | Description |
|---|---|---|
| `MAX_REPAIR_ATTEMPTS` | 2 | LLM repair loop iterations |
| `LLM_TEMPERATURE` | 0.0 | Sampling temperature |
| `DEFAULT_TIMEOUT_MS` | 60000 | Analysis timeout (ms) |
| `DEFAULT_REVIEW_THRESHOLD` | 0.6 | Confidence threshold for `review_required` |
| `DEFAULT_CACHE_TTL_SECONDS` | 86400 | Cache TTL (seconds) |
| `TENANT_SCOPE_ROOT_NAMES` | `("Tenant",)` | Entity names treated as tenant roots |
| `TENANT_SCOPE_FIELD_REGEX` | `^tenant[_-]?(id\|key)$` | Denormalised tenant-reference field detector |
| `MIN_TENANT_FIELD_COVERAGE_FRACTION` | 0.5 | Threshold for `discriminator_field` multitenancy |
| `MIN_SHARD_FAMILY_SIZE` | 2 | Min members for a shard-family group |

## Notes

- **Secrets**: API keys are read from config/env; never persisted by this library.
- **AQL fragments**: helper methods return AQL text + bind variables; collection names are passed via bind parameters.
- **Graceful degradation**: without an LLM provider, the analyzer returns deterministic baseline inference with `review_required=True`.

## Integration evaluation (Docker ArangoDB)

Bring up a local ArangoDB:

```bash
docker compose up -d
```

Run integration tests (opt-in):

```bash
export RUN_INTEGRATION=1
export ARANGO_URL=http://localhost:18529
export ARANGO_DB=schema_analyzer_it
export ARANGO_USER=root
export ARANGO_PASS=openSesame
pytest -q -m integration
```
