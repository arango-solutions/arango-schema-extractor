# Changelog

## 0.2.0

Quality + contract release. The analyzer now carries a per-relationship
statistics block, hardens discriminator detection, guarantees complete
collection coverage on LLM output, and emits the canonical property-
and edge-collection key names every downstream consumer had been
renaming by hand.

### Tool contract changes (breaking)

- **#6 key rename.** Property mappings now emit `field` (was
  `physicalFieldName`). Relationship mappings now emit
  `edgeCollectionName` and MUST NOT emit `collectionName` — the JSON
  schema rejects the latter. Entity mappings still use
  `collectionName` (unchanged). See `tool_contract/v1/response.schema.json`.
  Consumers that previously ran a `_normalize_analyzer_pm` /
  `_normalize_props` shim can delete it.

### New features

- **#3 statistics block.** `AgenticSchemaAnalyzer` now stamps
  `metadata.statistics` with per-collection counts, per-entity
  `estimated_count`, and a per-relationship bundle of `edge_count`,
  `source_count`, `target_count`, `avg_out_degree`, `avg_in_degree`,
  `cardinality_pattern` (`1:1` / `1:N` / `N:1` / `N:M`) and
  `selectivity`. When no live DB is available
  `metadata.statistics_status = "skipped_no_db"` and `statistics` is
  absent. Bounded AQL cost: one `LENGTH` per collection, one filtered
  `COLLECT` per LABEL / GENERIC_WITH_TYPE subset.
- **#5 reconciliation step.** After the LLM returns, the analyzer
  diffs its collection coverage against the snapshot and backfills any
  missing collections via baseline inference. The merge is reported in
  `metadata.reconciliation` with `llm_covered_collections`,
  `snapshot_collections`, `backfilled_collections`, and `strategy`; a
  user-visible warning is appended. No-op when the LLM's output is
  already complete.

### Quality

- **#4 discriminator hardening.** `_pick_best_type_field` now rejects
  candidate type fields that look like identifiers (`*Id`, `*_id`,
  `uuid`, etc.), carry too many distinct values
  (`MAX_TYPE_FIELD_DISTINCT_VALUES=32`), or cover too little of the
  collection (`MIN_TYPE_FIELD_COVERAGE_FRACTION=0.80`). Single-distinct-
  value edge discriminators are still accepted under the
  single-value-edge fallback. New tunables live in `defaults.py`.
- **#2 richer index flags.** `physicalMapping[...].indexes[*]` now
  propagates `vci`, `deduplicate`, and `storedValues` from the raw
  ArangoDB index metadata. Vertex-Centric Indexes are excluded from the
  `indexed=True` heuristic on properties.

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
