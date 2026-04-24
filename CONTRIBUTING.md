# Contributing

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

## Running tests

```bash
# Unit tests (default)
pytest -q

# With verbose output
pytest -v

# Integration tests (requires Docker ArangoDB on port 18529)
docker compose up -d
export RUN_INTEGRATION=1
export ARANGO_URL=http://localhost:18529
export ARANGO_DB=schema_analyzer_it
export ARANGO_USER=root
export ARANGO_PASS=openSesame
pytest -q -m integration
```

## Linting and formatting

```bash
ruff check .           # lint
ruff format --check .  # format check
ruff format .          # auto-format
mypy schema_analyzer/  # type checking
```

## Project structure

```
schema_analyzer/
├── analyzer.py          # AgenticSchemaAnalyzer (main entry point)
├── baseline.py          # Deterministic inference (no LLM fallback)
├── cache.py             # Filesystem caching by schema fingerprint
├── cli.py               # CLI: tool mode + eval subcommand
├── conceptual.py        # ConceptualSchema dataclass
├── defaults.py          # Centralized tunable constants
├── docs.py              # Markdown documentation generator
├── domain_detect.py     # Domain (PG / LPG / hybrid) detection helpers
├── errors.py            # SchemaAnalyzerError
├── exports.py           # Transpiler export (Cypher)
├── mapping.py           # PhysicalMapping with AQL helpers
├── mcp_server.py        # MCP stdio server (arangodb-schema-analyzer-mcp)
├── multitenancy.py      # metadata.multitenancy classification
├── owl_export.py        # OWL Turtle export
├── reconcile.py         # Backfill collections the LLM omitted
├── shard_families.py    # physicalMapping.shardFamilies grouping
├── sharding_profile.py  # metadata.shardingProfile classification (0.5.0)
├── snapshot.py          # Physical schema introspection + fingerprints
├── statistics.py        # metadata.statistics (counts + cardinality)
├── tenant_scope.py      # tenantScope annotator (0.4.0)
├── tool.py              # Tool contract v1 entrypoint (run_tool)
├── tool_contract_v1.py  # JSON Schema validation
├── types.py             # Pydantic models (AnalysisMetadata, AnalysisResult)
├── utils.py             # Shared utilities (pascal_case, sha256, JSON extraction)
├── validation.py        # LLM output validation schema
├── workflow.py          # Generate → validate → repair loop
├── py.typed             # PEP 561 marker
├── eval/                # Evaluation harness
│   ├── domain_loader.py # Load domain specs from domains/
│   ├── generator.py     # Physical schema generator (PG + LPG variants)
│   ├── runner.py        # Eval orchestration and reporting
│   └── scoring.py       # F1, domain/range, mapping style scoring
├── providers/           # LLM provider implementations
│   ├── base.py          # LLMProvider protocol
│   ├── openai_provider.py
│   ├── anthropic_provider.py
│   └── openrouter_provider.py
└── tool_contract/v1/    # Bundled JSON Schema files (request / response)
```

## Guidelines

- Keep outputs deterministic (ordering, stable JSON).
- Do not log or persist secrets (API keys, credentials).
- Add/adjust tests for behavior changes (golden fixtures where appropriate).
- Use `defaults.py` for tunable constants — avoid scattering magic numbers.
- Use `pascal_case()` from `utils.py` — do not create local copies.
- Shared test helpers live in `tests/conftest.py` — prefer importing over duplicating.
- Provider implementations should use `raise ... from e` for proper exception chaining.
- All tool responses (including unexpected errors) must be contract-shaped JSON.

## Adding a new LLM provider

1. Create `schema_analyzer/providers/my_provider.py` implementing the `LLMProvider` protocol
2. Register in `schema_analyzer/providers/__init__.py` `_REGISTRY`
3. Use constants from `defaults.py` for temperature, max_tokens, etc.
4. Wrap SDK errors as `SchemaAnalyzerError(code="PROVIDER_ERROR")` with `raise ... from e`

## Adding a new domain pack

1. Create `domains/<name>/domain.json` with entities and relationships
2. The eval harness auto-discovers domains via `list_domains()`
3. Run `arangodb-schema-analyzer eval --domains <name>` to test
