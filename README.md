# arangodb-schema-analyzer (v0.1)

Standalone Python library that analyzes an ArangoDB database’s physical schema and produces:

- a **conceptual schema** (entities, relationships, properties)
- a **conceptual→physical mapping** suitable for transpilers (Cypher, SPARQL, future)
- **metadata** (confidence, timestamp, analyzed collection counts, detected patterns)

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

If you don’t install a provider SDK (or you don’t provide an API key), analysis degrades gracefully.

## Usage

```python
from arango import ArangoClient

from schema_analyzer import AgenticSchemaAnalyzer

client = ArangoClient(hosts="http://localhost:8529")
db = client.db("mydb", username="root", password="openSesame")

analyzer = AgenticSchemaAnalyzer(
    llm_provider="openai",  # or "anthropic"
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

## Public API

Exports:
- `AgenticSchemaAnalyzer`
- `ConceptualSchema`
- `PhysicalMapping`
- `generate_schema_docs(analysis)`
- `export_mapping(analysis, target)`

## Notes
- **Secrets**: API keys are read from config/env; never persisted by this library.
- **AQL fragments**: helper methods return AQL text + bind variables; collection names are passed via bind parameters.

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


