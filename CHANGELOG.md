# Changelog

## Unreleased

### Transpiler export contract (PRD §6.4, adapter integration)

- **SPARQL export now emits `datatypeProperties`.** `export_mapping(target="sparql")`
  previously emitted only `classes` + `objectProperties`; it now also lists each
  entity's literal attributes as datatype properties (`iri`, `localName`,
  `label`, `domain`, `attribute`, `physical`), so a SPARQL→AQL transpiler can
  resolve literal-valued triple patterns (`?u :email ?e`) to a document-field
  access on the owning collection — not just entity/relationship patterns. The
  `SparqlExport` response-schema `$def` documents the new array (both the bundled
  and published copies, kept byte-identical). Additive and backward compatible.
- **New `docs/transpiler-integration.md`** — the contract for transpiler authors:
  how to consume the Cypher resolution index (`resolve`) and the SPARQL
  vocabulary view (`export`) to generate injection-safe AQL, including variable
  renaming, the `error`-instead-of-`aql` incomplete-mapping path, and datatype vs
  object property resolution. Documents that this library produces the *map*;
  query translation stays in the transpiler (e.g. `arango-cypher`).

### MCP remote transports + auth (PRD §3.11)

- The MCP server (`schema_analyzer/mcp_server.py`) now serves **sse** and
  **streamable-http** in addition to **stdio**, selectable via
  `--transport / --host / --port` (env fallbacks
  `SCHEMA_ANALYZER_MCP_TRANSPORT / _HOST / _PORT`). stdio remains the default,
  so existing invocations are unchanged.
- **Bearer-token auth for remote transports.** Set `SCHEMA_ANALYZER_MCP_TOKEN`
  and every HTTP request must carry `Authorization: Bearer <token>`
  (constant-time compared; missing/invalid → `401 UNAUTHENTICATED`). When
  unset, the server starts but logs a loud warning. The existing `run_tool`
  trust boundary (`SCHEMA_ANALYZER_ALLOWED_HOSTS` / `SCHEMA_ANALYZER_CACHE_ROOT`)
  is enforced for every transport.
- **Typed per-operation tools.** Alongside the generic `arangodb_schema_analyzer_run`
  / `_run_json`, the server now registers
  `schema_analyzer_snapshot | analyze | export | docs | owl` matching the §3.11
  surface, so MCP clients get typed parameters instead of one opaque request dict.

### Confidence calibration from eval feedback (PRD §3.12.3 / §6.5)

- New `schema_analyzer/eval/calibration.py` pairs each eval run's
  self-reported `metadata.confidence` with its realized quality (mean of
  entity / relationship / domain-range F1 and mapping-style accuracy) to
  measure whether confidence is calibrated and where the review gate should
  sit. Pure and DB-free — operates on report-entry dicts, so it runs on live
  results or a saved report and is unit-tested without ArangoDB.
  - `compute_calibration(entries)` returns a reliability curve (per-bin
    predicted-confidence vs observed-quality), **ECE** / **MCE** / **Brier**
    summaries, an overconfidence `gap` (mean confidence − mean quality), and a
    `recommended_review_threshold` derived by maximizing Youden's J of the
    `review_required = confidence < threshold` gate against a binary
    "good run" label (`observed quality ≥ quality_target`). Inputs, formula,
    and failure modes (empty input, all-good / all-bad non-discriminative
    cases) are documented per the §3.12.3 requirement.
  - `format_calibration_report(cal)` renders a human-readable table; the
    `eval` CLI now prints calibration after the score table.
  - **Eval report shape:** `save_eval_report` now writes
    `{"runs": [...], "calibration": {...}}` instead of a bare list.
    `compare_reports` reads both shapes (legacy list baselines still diff) and
    appends a calibration-drift section (gap / ECE / Brier / recommended
    threshold, baseline vs current) so drift is visible release-over-release.
  - New exports from `schema_analyzer.eval`: `compute_calibration`,
    `format_calibration_report`, `observed_quality`, `calibration_from_results`.
  - New tunables in `defaults.py`: `DEFAULT_CALIBRATION_BINS` (10),
    `DEFAULT_CALIBRATION_QUALITY_TARGET` (0.7).

## 0.7.0

### Internal refactor (no behavior change)

- Split the two largest modules for maintainability and isolated testing:
  `analyzer.py` (894→648 lines) extracted its post-inference enrichment pipeline
  into `enrichment.py`; `snapshot.py` (928→696 lines) extracted its DB-free
  type-discriminator heuristics into `type_detection.py`. Public import paths
  are preserved.

### CLI convenience commands

- New subcommands that connect to a database and emit a single artifact without
  hand-authoring v1 request JSON: `arangodb-schema-analyzer snapshot|analyze|docs|owl`
  with `--url/--database/--user/--password|--password-env-var`, plus
  `--provider/--model/--api-key-env-var` (analyze/docs/owl) and `--format`
  (owl). The default stdin/stdout tool mode and `eval` are unchanged.
- **Contract fix:** `metadata.vci` / `metadata.rdfTopology` are now declared
  nullable in the v1 response schema (they serialize as `null` when no signal
  is found); a baseline analyze response previously failed internal validation.

### New v1 tool-contract operations

- **`diff`** — structural diff of `input.previousAnalysis` vs `input.analysis`
  (wraps `diff_analyses`); result under `result.diff`.
- **`resolve`** — Cypher AQL resolution index from `input.analysis` (wraps
  `build_cypher_resolution_index`); result under `result.resolution`.
  Both promote previously library-only helpers to first-class operations.

### Richer OWL export (PRD §6.3)

- Turtle export now emits **`rdfs:subClassOf`** hierarchy from shard families,
  **`owl:FunctionalProperty` / `owl:InverseFunctionalProperty`** +
  `phys:observedCardinality` derived from the statistics cardinality pattern, and
  **`owl:inverseOf`** from an explicit relationship `inverseOf` field.
- New **JSON-LD export**: `export_conceptual_model_as_jsonld(analysis)` and the
  `owl` operation's `outputOptions.owlFormat: "jsonld"` (returns `result.jsonld`).

### Roadmap detections

- **RDF-topology (RPT) detection + TRIPLE mapping style** (PRD §6.1/§6.2).
  Deterministic, snapshot-only. Recognizes RDF triple/quad stores via collection
  naming (`_triples`, `quads`, …) or a subject/predicate/object field signature,
  and `rdf:type` assertion edges via sampled predicate values. Emits
  `metadata.rdfTopology` and annotates affected physical-mapping entries with
  `tripleCandidate: true` + a `triple` (`{"style": "TRIPLE"}`) block alongside the
  native style.
- **Vertex-Centric Index (VCI) detection** (PRD §6.1/§6.2). Deterministic,
  snapshot-only. Relationship mappings whose edge collection carries a
  persistent index rooted at `_from`/`_to` plus discriminator fields, and/or
  edge attributes that duplicate endpoint-vertex properties, gain a `vci` block
  (`indexLevel` with access pattern out-edge/in-edge/both + participating
  fields; `denormalization` with duplicated fields and source collections) and
  `vciCandidate: true` *alongside* the existing style. A `metadata.vci` summary
  lists the relationships involved. Also fixes a latent gap where the LLM path
  dropped `metadata.multitenancy` from the typed result.

### Transpiler, lineage & egress features (Milestone B)

- **SPARQL export target.** `export_mapping(analysis, target="sparql")` (and the
  v1 `export` operation with `outputOptions.exportTarget="sparql"`) emits an RDF
  vocabulary view — classes, object properties with IRIs/domains/ranges —
  annotated with the physical mapping so a SPARQL→AQL transpiler can resolve
  triple patterns to collections and edge traversals. The response contract
  gains a `SparqlExport` shape.
- **Cypher resolution adapter.** New `build_cypher_resolution_index(analysis)`
  precomputes, per entity label and relationship type, the injection-safe AQL
  match/traversal fragment a Cypher transpiler would otherwise re-derive;
  incomplete mappings are reported with an `error` block instead of crashing.
- **Analysis diff.** New `diff_analyses(previous, current)` (PRD §3.13.3)
  returns added/removed/changed entities and relationships, mapping-style flips,
  and the health-score delta — for stale detection and re-analysis workflows.
- **Element-level provenance.** Every conceptual entity/relationship and
  physical-mapping entry is tagged with `source` (`llm` / `baseline` / `human`,
  PRD §3.13.2); reconciliation-backfilled collections are correctly attributed
  to `baseline` even on an LLM run, and pre-existing `human` tags are preserved.
- **LLM redaction modes.** New `analysisOptions.redaction` (`stripSamples`,
  `maskFieldValues`, PRD §4.3) scrubs sampled documents and concrete field
  values from the snapshot before LLM egress while preserving structure; the
  local snapshot used for baseline/reconciliation/statistics is unaffected.

### Quality & developer experience (Milestone A)

- **Quality metrics + health score.** Every analysis now stamps
  `metadata.qualityMetrics` (deterministic structural signals — connectivity,
  orphan ratio, property richness, dangling-relationship consistency — plus
  grounding signals that check the physical mapping against the snapshot) and
  a normalized 0–100 `metadata.healthScore` composite (PRD §3.12.3). See
  `schema_analyzer/quality.py`. The v1 response contract documents both fields
  (`metadata` already allowed additional properties, so this is backward
  compatible).
- **mypy is now a blocking CI gate.** The `pydantic.mypy` plugin is enabled and
  the previously-tolerated type backlog (python-arango `Result[...]` unions,
  alias mismatches, `Any` leaks) was cleared. A small typed adapter,
  `schema_analyzer/_arango.py`, centralizes the synchronous-path casts.
- **Coverage floor raised 65% → 80%**, with new tests for the LLM retry/repair
  loop, the async analysis path, and the stdlib OpenRouter provider.
- **Contract-parity test.** `tests/test_tool_contract_schema_parity.py` asserts
  the documented (`docs/tool-contract/v1`) and bundled
  (`schema_analyzer/tool_contract/v1`) JSON schemas stay byte-identical.
- **Async path verified.** All shipped providers implement `agenerate`; the
  async `analyze_physical_schema_async` entrypoint now has end-to-end coverage.

## 0.6.1

Bugfix and hardening release. No new user-facing features and no
intentional breaking changes; existing v1 contract callers and
programmatic consumers continue to work unchanged.

### Tool contract (v1)

- **Drop `outputOptions.pretty`** from the request schema. The field was
  declared in 0.6.0 but never read by `run_tool`; pretty-printing has
  always been a CLI / caller concern. Strict callers passing `pretty`
  must remove it to keep validating.
- **Honor `outputOptions.includeSnapshotFingerprint`.** Default `true`
  (previous behavior). Setting `false` now actually omits
  `tooling.snapshotFingerprint` from the response.
- **Bound request size.** `maxLength` on string fields (URL ≤ 2048,
  password ≤ 1024, system prompt ≤ 65536, etc.) and `maxItems` on
  request-side analysis arrays (`entities` / `relationships` ≤ 50000,
  `properties` ≤ 100000). Closes a DoS surface on any process exposing
  the v1 contract over MCP / RPC. Requests within historical sizes are
  unaffected.

### Security hardening

- **Cache directory containment.** New optional `SCHEMA_ANALYZER_CACHE_ROOT`
  env var. When set, every resolved cache directory must live under the
  configured root; `..` traversal is rejected.
- **Connection host allowlist.** New optional
  `SCHEMA_ANALYZER_ALLOWED_HOSTS` env var (comma-separated host[:port]).
  When set, `connection.url` requests targeting other hosts are
  rejected before any database call. Unset preserves the historical
  trust-the-caller behavior for local CLI use.
- **Cache file permissions.** Filesystem cache writes are now `chmod 0o600`
  on POSIX hosts so cached samples are owner-only on shared systems.
- **OpenRouter provider hardening.** HTTP error bodies are scrubbed for
  bearer-token / `api_key` / `sk-…` patterns before being attached to
  `SchemaAnalyzerError`; HTTP redirects are blocked via a custom opener
  so a typo'd `base_url` cannot silently relay credentials to a
  different host.
- **LLM output allowlist.** Reconciliation now strips any
  `collectionName` / `edgeCollectionName` produced by the LLM that does
  not appear in the live snapshot, before backfill runs. Bind-parameter
  AQL already prevented injection; this closes the integrity gap so
  downstream consumers cannot trust hallucinated names.

### Internal refactor (no behavior change)

- Consolidated three near-duplicate `entity_property_names` helpers into
  one in `utils.py`; fixes a latent bug where dict-shaped properties
  were silently skipped in `tenant_scope`.
- Added shared helpers: `normalize_analysis_dict`,
  `iter_edge_definitions`, `index_edge_definitions_by_collection`,
  `split_domain_tokens` in `utils.py`; `import_optional_sdk` and
  `wrap_provider_call` in `providers/base.py`.
- Unified sync / async retry policy in `workflow.py` via a shared
  `_retry_decision` helper.
- Routed all hardwired tunables through `defaults.py`
  (`BASELINE_NO_LLM_CONFIDENCE`, `OPENROUTER_*`, `DEFAULT_*_MODEL`,
  `STATISTICS_*`, `SNAPSHOT_FORMAT_VERSION`, `DEFAULT_OWL_*_IRI`,
  `DEFAULT_EVAL_*`).
- Pre-compiled the default tenant-collection naming regex in
  `multitenancy.py`.
- Added `fresh_database` pytest fixture in `tests/conftest.py`;
  integration tests now reuse it.

### Documentation

- README: documented `--verbose` long form and the `[dev]` /
  `[openrouter]` extras; fixed regex backtick escaping in the config
  table.
- PRD: marked the MCP server as shipped (no longer "future"); widened
  the testing matrix to Python 3.10–3.13 to match the classifiers;
  documented the new env-driven security knobs in §4.3.
- CONTRIBUTING: corrected the project tree to list every `__init__.py`;
  documented `scripts/run_reverse_engineering_eval.py` as an ad-hoc
  developer runner.
- `.env.example`: clarified that `ARANGO_HOST` / `ARANGO_PASSWORD`
  fallbacks apply only to the eval CLI, plus examples for the new
  `SCHEMA_ANALYZER_*` env vars.
- `docs/tool-contract/v1/`: synchronized the schema and example copies
  with the bundled package versions.

### Tests

- 353 unit tests passing (32 integration tests skip without
  `RUN_INTEGRATION=1`). New coverage in `tests/test_tool_security.py`,
  `tests/test_phase2_helpers.py`, and the expanded `tests/test_utils.py`.

## 0.6.0

Additive, non-breaking. Existing exports continue to validate against
the v1 contract unchanged.

### New features

- **Shard-family detection (`physicalMapping.shardFamilies`).**
  Implements PRD §6.2 bullet 5. A *shard family* groups conceptual
  entities that share an identical property set and a common
  CamelCase / snake_case suffix — the structural fingerprint of the
  per-source / per-repository / per-stream collection-duplication
  pattern (e.g. `IBEX_Documents` / `MAROCCHINO_Documents` /
  `MOR1KX_Documents` / `OR1200_Documents` ⇒ family `Document`).

  Detection is deterministic, snapshot-only (no DB round-trip, no LLM
  call). Each family carries:

  - `name` / `suffix` — family label and the verbatim shared suffix.
  - `discriminator` — `{source: "field", field: <name>}` when every
    member declares a candidate discriminator field (default
    candidates: `repo`, `source`, `stream`, `upstream`); otherwise
    `{source: "collection_prefix"}` and the discriminator value is
    drawn from each member's name prefix.
  - `sharedProperties` — sorted property-name list (the bucket key).
  - `members[]` — for each member entity, its conceptual name,
    underlying `collectionName`, and `discriminatorValue` (the
    name-prefix portion).

  Downstream impact: NL→Cypher prompt builders and UI mapping panels
  can now emit UNION-aware guidance instead of silently picking one
  member alphabetically (the IBEX/MAROCCHINO/MOR1KX/OR1200 first-in-
  summary bias, defect D7 in `arango-cypher-py/docs/schema_inference_bugfix_prd.md`).

  Output is sorted by `(name, suffix)` for deterministic golden-snapshot
  output. `shardFamilies` is omitted entirely when the input has no
  usable entity dict; an empty list (`[]`) means detection ran and
  found no families (consumers can distinguish "didn't run" from "ran,
  found none").

### Tunables (`schema_analyzer/defaults.py`)

- `MIN_SHARD_FAMILY_SIZE` (default `2`) — minimum members for a family.
- `MIN_SHARD_FAMILY_SUFFIX_LEN` (default `4`) — minimum suffix length;
  short suffixes like `Op`/`Tx` are too noisy to be useful.
- `SHARD_FAMILY_DISCRIMINATOR_FIELDS` (default `("repo", "source",
  "stream", "upstream")`) — case-insensitive candidate field names
  probed in order; first one carried by *every* member wins.

### Backward compatibility

- `PhysicalMapping.shard_families` is `None` by default. `to_json`
  omits the `shardFamilies` key when `None`, preserving byte-identity
  with pre-detector output for callers that build mappings by hand.
- v1 response schema gains an additive `shardFamilies` array under
  `physicalMapping`. Existing fixtures continue to validate (the field
  is optional).
- No cache invalidation needed: `physical_fingerprint` is unchanged
  and the new field is purely additive.

## 0.5.0

Additive, non-breaking. Existing exports continue to validate against
the v1 contract unchanged.

### New features

- **Sharding-profile classification (`metadata.shardingProfile`).**
  First landing of the PRD §6.2 "Sharding-pattern detection" bullet
  (spec committed in `b3d4744`). The analyzer now classifies every
  analyzed database into one of five exclusive deployment styles, once
  per analysis:

  - `OneShard` — database-level `sharding == "single"`. Single-shard
    databases where cross-collection traversal never crosses DBServers.
    Carries an `oneShardLeader` hint when every user collection shares
    a consistent `distributeShardsLike` leader.
  - `DisjointSmartGraph` — at least one named graph is both
    `isSmart == true` and `isDisjoint == true`. The canonical
    ArangoDB multi-tenant pattern: traversal across the disjoint
    attribute is forbidden by the storage layer.
  - `SmartGraph` — at least one smart (but non-disjoint) named graph.
    Vertex collections share the smart attribute as their shard key;
    edge traversals are locality-aware.
  - `SatelliteGraph` — every user collection is a satellite (typical
    of meta-graph / ontology / reference databases).
  - `Sharded` — fall-through default for everything else; standard
    hash-sharded collections.

  Classification is deterministic and snapshot-only — no new DB round
  trip. Missing fields (older ArangoDB versions, restricted users
  whose `db.properties()` returned partial data) degrade to `Sharded`
  with `shardingProfile.status == "degraded"` and a human-readable
  `statusReason` instead of raising.

  The block carries per-graph evidence (`graphs[*].{isSmart,
  isDisjoint, smartGraphAttribute, vertexCollections,
  edgeCollections}`), per-collection evidence
  (`collections[*].{kind, numberOfShards, shardKeys,
  replicationFactor, distributeShardsLike, smartGraphAttribute,
  isDisjoint, graphName}`), the database-level properties the
  classifier used (`database.{sharding, replicationFactor,
  writeConcern}`), and a `collectionKindCounts` summary so downstream
  consumers can branch on the breakdown without iterating the full
  collections map.

  `metadata.shardingProfileStatus` mirrors `shardingProfile.status`
  for callers that only need the `"ok"` / `"degraded"` bit, matching
  the existing `metadata.statisticsStatus` convention.

- **Snapshot now carries `database` block and SmartGraph flags on
  graphs.** `snapshot["database"]` captures `{name, sharding,
  replicationFactor, writeConcern}` from `db.properties()`;
  `snapshot["graphs_detailed"][*]` now also carries `isSmart`,
  `isDisjoint`, `smartGraphAttribute`, `isSatellite` when the server
  exposes them. Unlocks the sharding-profile classifier without
  another DB probe; backwards-compatible (older consumers simply
  ignore the new keys).

### Backward compatibility

- The `database` block defaults to `{}` when `db.properties()` fails
  or is unavailable. Snapshot JSON schema is permissive
  (`additionalProperties: true`), so no consumer needs to change.
- `metadata.shardingProfile` is optional in the v1 response schema;
  pre-Unreleased cached analysis results load unchanged.
- One-time analysis-cache invalidation on upgrade.
  `fingerprint_physical_schema` hashes the whole snapshot dict, so
  adding `snapshot["database"]` and the per-graph SmartGraph flags
  changes every fingerprint exactly once; the first analysis after
  upgrade re-runs the LLM workflow and then caches normally. This is
  the standard behaviour whenever a new structural field lands — it
  matches the cache-reset behaviour of prior snapshot extensions
  (e.g. VCI flags in 0.3.0).

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
