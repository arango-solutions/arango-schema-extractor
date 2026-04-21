# Changelog

## 0.4.0

Additive, non-breaking. Existing single-tenant exports continue to
validate against the v1 contract unchanged.

### New features

- **#13 tenant-scope annotations.** The analyzer now classifies every
  entity in the physical mapping by its tenant role and stamps the
  result onto `physicalMapping.entities[*].tenantScope`. Three roles
  are recognised:

  - `tenant_root` — the entity that anchors the tenant hierarchy
    (default name: `Tenant`; configurable via
    `SCHEMA_ANALYZER_TENANT_ROOT_NAMES`).
  - `tenant_scoped` — belongs to a single tenant. Carries
    `tenantField` when the entity has a denormalised tenant-reference
    column (default regex: `^tenant[_-]?(id|key)$`, configurable via
    `SCHEMA_ANALYZER_TENANT_FIELD_REGEX`); omits it when the entity
    is only reachable from the tenant root via traversal.
  - `global` — cross-tenant reference data (e.g. `Cve`,
    `AppVersion`). Consumers MUST NOT add a tenant filter to queries
    against these collections.

  Detection is deterministic and depends only on the conceptual
  schema + physical mapping (no LLM call). It runs after
  reconciliation so backfilled entities are also classified, and
  before validation so the response-schema check covers the new
  field. Operators can pre-stamp `tenantScope` on any entry to
  override the heuristic — explicit annotations always win. BFS depth
  for traversal-only classification is capped at
  `TENANT_SCOPE_MAX_HOPS` (default 5; env-var overridable).

  A per-run summary lands in `metadata.tenantScopeReport` with
  `tenantEntity`, `denormScopedCount`, `traversalScopedCount`,
  `globalCount`, `tenantFieldRegex`, and a `discovery` breakdown by
  source (explicit / denorm-field / traversal).

  No-op (and no metadata block) when no tenant root is detected, so
  single-tenant graphs are byte-identical to their `0.3.0` exports.

### Tool contract additions (additive)

- `physicalMapping.entities[*].tenantScope` is now defined as an
  optional object with `role` (enum: `tenant_root` /
  `tenant_scoped` / `global`), optional `tenantField`, and optional
  `tenantEntity`. See the new `TenantScope` `$def` in
  `tool_contract/v1/response.schema.json`.
- `metadata.tenantScopeReport` is now defined as an optional object
  carrying the per-run classification summary.

### Configuration

New tunables in `defaults.py`:

- `TENANT_SCOPE_ROOT_NAMES = ("Tenant",)`
- `TENANT_SCOPE_FIELD_REGEX = r"^tenant[_-]?(id|key)$"`
- `TENANT_SCOPE_MAX_HOPS = 5`

Each is overridable at runtime via the matching
`SCHEMA_ANALYZER_TENANT_*` environment variable.

## 0.3.0

First PyPI release. Consolidates the quality + contract work originally
slated for `0.2.0` (issues #2-#6) with the cheap schema-change probes
(#7) and PRD amendment (#8) that landed on `main` shortly after.
Version `0.2.0` was prepared on `main` but never published; `0.3.0` is
the first tag to reach PyPI.

### Tool contract changes (breaking)

- **#6 key rename.** Property mappings now emit `field` (was
  `physicalFieldName`). Relationship mappings now emit
  `edgeCollectionName` and MUST NOT emit `collectionName` — the JSON
  schema rejects the latter. Entity mappings still use
  `collectionName` (unchanged). See `tool_contract/v1/response.schema.json`.
  Consumers that previously ran a `_normalize_analyzer_pm` /
  `_normalize_props` shim can delete it.

### New features

- **#7 cheap schema-change probes.** Two new top-level helpers in
  `schema_analyzer.snapshot` (re-exported from the package root):
  - `fingerprint_physical_shape(db, *, exclude_collections=None)` — hashes
    only the user-collection set, per-collection type (document vs edge),
    and per-collection sorted index digests (`type`, `fields`, `unique`,
    `sparse`, `vci`, `deduplicate`). Auto-generated index `name` / `id`
    are excluded so restarts and rebuilds don't produce false positives.
    Stable under ordinary INSERT / UPDATE / REMOVE writes.
  - `fingerprint_physical_counts(db, *, exclude_collections=None)` —
    shape fingerprint combined with `col.count()` per included
    collection; changes whenever the shape or any row count changes.
  Both probes read only python-arango primitives (`db.collections()`,
  `col.indexes()`, `col.count()`) — no AQL, no samples, no analyzer
  logic — so consumers can answer "has it changed?" in a few dozen
  milliseconds instead of running the full `snapshot_physical_schema`.
  Collection-level failures degrade gracefully (sentinel contribution)
  rather than raising. `exclude_collections` lets callers using a
  database-resident cache self-exclude their bookkeeping collection.
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

### Documentation

- **#8 PRD §3.13.3 / §4.1 update.** The PRD now sanctions the two-
  fingerprint model (shape vs counts), a four-state change-status
  contract (`unchanged` / `stats_changed` / `shape_changed` /
  `no_cache`), stats-only refresh as the product behavior for
  `stats_changed`, storage-agnostic caching, and self-exclusion of
  database-resident cache collections from the shape fingerprint.

### CI / Release infrastructure

- Trusted-publisher GitHub Actions workflow (`publish.yml`) targeting
  PyPI and TestPyPI via OIDC — no long-lived tokens.
- `sdist` allow-list tightened in `pyproject.toml` so source
  distributions include only package code, licence, readme, changelog,
  and the tool-contract JSON schemas.
- `schema_analyzer/py.typed` added so downstream type checkers pick up
  the package's inline annotations.
- Ruff lint + format are enforced by CI; mypy runs in advisory mode.

## 0.2.0 (never published)

`0.2.0` was bumped on `main` as the planned first PyPI release but the
tag was never cut — #7 and #8 landed before release and the scope was
rolled forward into `0.3.0`. Everything originally slated for `0.2.0`
is part of `0.3.0` above.

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
