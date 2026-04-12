# Changelog

## 0.1.0

### Initial release

- Physical schema snapshotting with deterministic ordering and fingerprinting
- Conceptual schema inference (entities, relationships, properties)
- Physical mapping generation (COLLECTION, LABEL, DEDICATED_COLLECTION, GENERIC_WITH_TYPE)
- AQL fragment helpers (`aql_entity_match`, `aql_relationship_traversal`) with injection-safe bind parameters
- LLM-assisted analysis with generate → validate → repair loop
- Provider support: OpenAI, Anthropic, OpenRouter (pluggable registry)
- Deterministic baseline inference when no LLM is configured (graceful degradation)
- Filesystem caching keyed by schema fingerprint with configurable TTL
- Tool contract v1: stable JSON API (stdin/stdout) with request/response schema validation
- CLI: tool mode (stdin JSON) and eval subcommand
- Output formats: analysis JSON, snapshot, export (Cypher), Markdown docs, OWL Turtle
- Evaluation harness with 5 domain packs, physical schema generator, and F1/accuracy scoring
- Eval report comparison for tracking quality regressions

### Quality improvements

- Centralized tunable defaults in `defaults.py` (LLM parameters, timeouts, confidence, cache)
- Unified `pascal_case()` utility replacing duplicate implementations
- Shared test helpers extracted into `conftest.py`
- Consolidated sync/async workflow via shared `_parse_and_validate()` helper
- Catch-all error handler in tool entrypoint for contract-shaped error responses
- Eliminated redundant snapshot work (tool passes pre-built snapshot to analyzer)
- TYPE_CHECKING guards for `StandardDatabase` imports
- Proper exception chaining (`raise ... from e`) across all providers and workflow
- Logging in tool.py (operation tracking) and cache.py (corrupt file warnings)
- CI: pip caching, integration tests trigger on PRs, coverage threshold at 65%
- Test coverage for: cache, docs, exports, OWL export, validation, providers, conceptual schema, CLI, tool happy paths
