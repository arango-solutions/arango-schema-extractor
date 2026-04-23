# Product Requirements Document (PRD)

## **Project Name:** ArangoDB Schema Analyzer

**Package**: `arangodb-schema-analyzer` (PyPI)
**Import**: `schema_analyzer`
**Language**: Python ≥ 3.10
**Version**: 0.1.0

**Companion codebase:** **Arango-OntoExtract (AOE)** is developed in the **`ontology_generator`** repository (underscore, not `ontology-generator`). A typical local layout is `~/code/ontology_generator` alongside this repo. Cross-references in this document mean that project.

---

### **1. Executive Summary**

The **ArangoDB Schema Analyzer** is a standalone Python library that uses algorithmic heuristics and Large Language Models (LLMs) to reverse-engineer a **conceptual model** from an existing ArangoDB physical schema. It simultaneously generates a machine-readable **mapping layer** that links conceptual entities to their physical implementations. This enables language transpilers (Cypher, SPARQL, SQL) to generate correct AQL for schemas that mix Property Graph and Labeled Property Graph patterns.

The system operates as:
- A **Python library** (`AgenticSchemaAnalyzer`)
- A **non-interactive CLI tool** (`arangodb-schema-analyzer`) using stdin/stdout JSON per the v1 tool contract
- An **evaluation harness** for benchmarking analysis quality across domain packs
- An **optional MCP server** (post–v0.1; see §3.11) that exposes the same operations as MCP tools for AI agents and IDEs

**Quality and lineage (directional):** v0.1 ships **metadata confidence**, **review gating**, and **offline eval F1 scores** against domain packs. Broader **ontology-quality metrics** and **temporal provenance** for re-analysis and schema drift are specified in §3.12 and §3.13 to align with the patterns used in **Arango-OntoExtract (AOE)** (`ontology_generator` — multi-signal scoring, health score, extraction run records, and temporal diff).

---

### **2. Problem Statement**

ArangoDB schemas "in the wild" follow different modeling conventions:

* **Property Graph (PG):** Typed vertex/edge collections (e.g., `Users`, `Follows`) — one collection per concept.
* **Labeled Property Graph (LPG):** Generic collections with discriminator fields (e.g., a single `entities` collection with a `type` field distinguishing `User`, `Post`, etc.).
* **Hybrid:** Combinations where some concepts use dedicated collections and others share generic collections with type fields.

Without a unified map of *how* a concept is physically implemented, transpilers cannot generate correct AQL. This library produces that map.

---

### **3. Functional Requirements**

#### **3.1. Physical Schema Introspection**

The system must connect to an ArangoDB database and produce a deterministic **physical schema snapshot** containing:
- Collection metadata (name, type, count, properties, indexes)
- Named graph definitions (edge definitions, orphan collections)
- Candidate type fields detected from sample documents
- Sample field value distributions for type-field inference

**Implementation**: `snapshot_physical_schema()` in `snapshot.py`.

#### **3.2. Conceptual Schema Inference**

The system must produce a **conceptual schema** with:
- **Entities**: named types with labels and properties (e.g., `User`, `Post`)
- **Relationships**: typed directed edges between entities (e.g., `FOLLOWS: User → User`)
- **Properties**: global property definitions

**Implementation**: `ConceptualSchema` dataclass in `conceptual.py`.

#### **3.3. Physical Mapping Generation**

The system must produce a **conceptual → physical mapping** describing how each conceptual entity/relationship maps to ArangoDB collections:

| Mapping Style | Entity/Relationship | Description |
|---|---|---|
| `COLLECTION` | Entity | One document collection per entity type |
| `LABEL` | Entity | Shared collection filtered by `typeField == typeValue` |
| `DEDICATED_COLLECTION` | Relationship | One edge collection per relationship type |
| `GENERIC_WITH_TYPE` | Relationship | Shared edge collection filtered by `typeField == typeValue` |

**Implementation**: `PhysicalMapping` dataclass in `mapping.py`, with `aql_entity_match()` and `aql_relationship_traversal()` helper methods that produce injection-safe AQL fragments.

#### **3.4. Hybrid Pattern Detection**

The system must automatically detect and classify physical schema patterns:

- **PG (Property Graph):** Distinct vertex collections, no discriminator fields → `COLLECTION` / `DEDICATED_COLLECTION` mapping.
- **LPG (Labeled Property Graph):** Generic node/edge collections with discriminator fields (`type`, `kind`, `label`, `relation`, `relType`) → `LABEL` / `GENERIC_WITH_TYPE` mapping.

Detection uses candidate type field analysis from `sample_field_value_counts` in the snapshot.

**Implementation**: Baseline heuristic inference in `baseline.py` (`infer_baseline_from_snapshot()`).

#### **3.5. Agentic Semantic Enrichment (LLM)**

When an LLM provider is configured, the system uses it to:
- Infer richer semantics than heuristics alone (entity naming, relationship endpoint resolution)
- Generate a conceptual schema + mapping from the physical snapshot
- Self-repair via a generate → validate → repair loop

When no LLM is available (no provider or no API key), the system **degrades gracefully** to deterministic baseline inference.

**Human-in-the-loop:** `metadata.review_required` (with `confidence` vs `reviewThreshold`) signals that outputs are **provisional**. This library does not implement curation UIs; consumers (e.g. **AOE** in `ontology_generator`) own approval, edit, and promotion workflows.

**Implementation**: `AgenticSchemaAnalyzer` in `analyzer.py`, with the generate-validate-repair loop in `workflow.py`.

#### **3.6. LLM Provider Support**

The system supports multiple LLM providers via a pluggable registry:

| Provider | SDK | Default Model |
|---|---|---|
| OpenAI | `openai` (optional extra) | `gpt-4o-mini` |
| Anthropic | `anthropic` (optional extra) | `claude-3-5-sonnet-latest` |
| OpenRouter | stdlib `urllib` (no extra) | `openai/gpt-4o-mini` |

Custom providers can be registered via `register_provider()`.

**Implementation**: `providers/` package with `LLMProvider` protocol in `providers/base.py`.

#### **3.7. Output Formats**

The system produces multiple output formats:

| Format | Operation | Description |
|---|---|---|
| Analysis JSON | `analyze` | Conceptual schema + physical mapping + metadata |
| Physical Snapshot | `snapshot` | Deterministic physical schema introspection |
| Export JSON | `export` | Stable JSON for transpiler consumption (v0.1: Cypher) |
| Markdown Docs | `docs` | Human-readable schema documentation |
| OWL Turtle | `owl` | Conceptual schema + physical mapping as OWL 2 ontology with `phys:` annotation properties |

**Implementation**: `docs.py`, `exports.py`, `owl_export.py`.

#### **3.8. Tool Contract (v1)**

The system exposes a **stable non-interactive JSON API** defined by JSON Schema:

- **Request schema**: `docs/tool-contract/v1/request.schema.json`
- **Response schema**: `docs/tool-contract/v1/response.schema.json`

Operations: `analyze`, `snapshot`, `export`, `docs`, `owl`.

All requests and responses are validated against the JSON Schema. Failures return structured `{ "ok": false, "error": { "code": "...", "message": "..." } }` responses for both expected errors (`SchemaAnalyzerError`) and unexpected exceptions.

**Implementation**: `tool.py` (entrypoint), `tool_contract_v1.py` (schema loading and validation).

#### **3.9. CLI**

Two modes:

1. **Tool mode** (default): `arangodb-schema-analyzer [--request FILE] [--pretty] [--out FILE]` — stdin/stdout JSON using the v1 contract.
2. **Eval mode**: `arangodb-schema-analyzer eval [--provider NAME] [--model NAME] [--domains LIST] [--report FILE] [--baseline FILE]` — run evaluation benchmarks.

**Implementation**: `cli.py`.

#### **3.10. Evaluation Harness**

The system includes a domain-based evaluation framework:

- **Domain packs** (in `domains/`): ground-truth conceptual models for healthcare, financial fraud detection, insurance, intelligence, and network asset management.
- **Physical schema generator**: materializes multiple physical schema variants (PG + LPG) from each domain spec with seeded sample data.
- **Scoring**: entity F1, relationship F1, domain/range F1, mapping style accuracy.
- **Report comparison**: diff two eval reports to track quality regressions/improvements.

**Implementation**: `eval/` package (`domain_loader.py`, `generator.py`, `runner.py`, `scoring.py`).

#### **3.11. MCP Server (optional integration surface)**

**Purpose:** Expose schema-analyzer operations through the **Model Context Protocol (MCP)** so MCP-capable clients (e.g. Cursor, custom agents) can call **analyze**, **snapshot**, **export**, **docs**, and **owl** without bespoke subprocess glue.

**Relationship to the v1 tool contract:** MCP tools are a **thin adapter** over the same inputs/outputs as `run_tool()` / CLI JSON (see §3.8). Parameter schemas should stay aligned with `docs/tool-contract/v1/request.schema.json` (subset per tool).

**Suggested MCP tools (illustrative):**

| MCP tool | Maps to operation | Notes |
|---|---|---|
| `schema_analyzer_snapshot` | `snapshot` | Connection + `analysisOptions` |
| `schema_analyzer_analyze` | `analyze` | Connection + optional `llm` + `analysisOptions` |
| `schema_analyzer_export` | `export` | `input.analysis` + `exportTarget` |
| `schema_analyzer_docs` | `docs` | `input.analysis` |
| `schema_analyzer_owl` | `owl` | `input.analysis` |

**Requirements:**

- **Secrets:** Prefer env-indirection (`passwordEnvVar`, `apiKeyEnvVar`) in tool parameters; document that inline secrets in MCP payloads inherit the same risks as inline JSON secrets (logs, transcripts).
- **Errors:** Surface contract-shaped errors (`ok: false`, typed `code`) to the client.
- **Transports:** Support at least **stdio** for local IDE use; **SSE** (or equivalent) optional for remote agents.
- **Implementation status:** Not part of v0.1 core deliverables; tracked as integration packaging (separate entrypoint or sibling package acceptable).

**Reference:** AOE documents a full MCP surface for ontology library and extraction (`ontology_generator` PRD §6.10); this project scopes MCP to **schema reverse-engineering and mapping** only.

#### **3.12. Quality metrics — extraction and ontology structure**

This library produces a **conceptual schema** and **physical mapping**, optionally serialized as **OWL Turtle** (§3.7). Quality measurement spans **offline benchmarks** (today) and **production-oriented metrics** (target, aligned with AOE §6.13 where applicable).

**3.12.1. Implemented (v0.1) — eval harness**

| Metric | Description |
|---|---|
| Entity F1 | Precision/recall/F1 over normalized entity names vs domain-pack ground truth |
| Relationship F1 | Over relationship types |
| Domain/range F1 | Over relationship signatures (type + endpoints) |
| Mapping-style accuracy | Agreement with expected COLLECTION / LABEL / DEDICATED_COLLECTION / GENERIC_WITH_TYPE patterns |

**3.12.2. Per-run metadata (v0.1)**

| Signal | Use |
|---|---|
| `metadata.confidence` | Single scalar; gating with `review_required` vs `reviewThreshold` |
| `metadata.warnings` / `assumptions` | Human review queues |
| `used_baseline`, `repair_attempts` | Explains whether LLM path ran and how much self-repair occurred |

**3.12.3. Target — richer ontology / extraction quality (roadmap)**

Inspired by **AOE** (`ontology_generator`): multi-signal confidence, structural ontology metrics, and composite scores. Mapped to **schema-derived** artifacts (no document chunks unless optional domain context is added later):

| Category | Intended metrics | Notes |
|---|---|---|
| **Structural (schema-derived)** | Relationship connectivity (entities participating in ≥1 relationship), orphan entity ratio, property richness on conceptual entities, cycle / inconsistency flags | Analogous to AOE “connectivity” and OntoQA-style structural checks, applied to conceptual graph + mapping completeness |
| **Grounding vs physical** | Agreement of mapping with snapshot (every mapped collection exists; type fields appear in `sample_field_value_counts` when sampled) | Faithfulness-to-schema analogue of AOE faithfulness-to-chunks |
| **Gold comparison** | Optional recall/precision vs a supplied reference OWL/TTL or domain pack | Analogous to AOE gold-standard recall (`POST /quality/recall` pattern) |
| **Composite** | Normalized **health score** (0–100) combining structural + confidence + optional gold overlap | Analogous to AOE ontology health score |
| **History** | Store metric snapshots per analysis run for trend lines | Analogous to AOE `quality_history` / dashboard history |

**Requirements (acceptance for “metrics complete” milestones):**

- Eval metrics remain the **regression gate** for releases (CI compares reports to baselines).
- Any new composite score must document **inputs, formula, and failure modes** (e.g. empty relationship set).
- Production metrics must not require sending full snapshots to third parties unless explicitly configured.

#### **3.13. Temporal provenance, runs, and schema-change lineage**

**Problem:** Consumers need to know **when** an analysis was produced, **what physical schema** it reflected, and **what changed** after a database or mapping evolves — especially for **ontology update cycles** driven by graph schema changes.

**AOE reference:** `ontology_generator` uses **extraction run** documents (`started_at`, `completed_at`, `model`, `prompt_version`, stats), **temporal versioning** on ontology entities (`created` / `expired` intervals), **point-in-time snapshot** APIs, and **temporal diff** between timestamps (PRD §5.3, §6.5, data model for `extraction_runs`, `quality_history`).

**3.13.1. Minimum viable (target for schema-analyzer)**

| Element | Requirement |
|---|---|
| **Analysis run identity** | Each `analyze` produces a stable **run id** (UUID) and records **ISO-8601 timestamps** for start/end (or single `completed_at` if synchronous). |
| **Physical schema linkage** | Persist **snapshot fingerprint** (existing SHA-256, §4.1) and **library + contract version** on the result object. |
| **LLM lineage** | When LLM is used: `provider`, `model`, optional **`promptVersion`** / **system prompt hash** (no raw prompt in logs by default). |
| **Reproducibility** | Cache hits remain keyed by fingerprint; run records distinguish “served from cache at time T” vs “fresh LLM at time T”. |

**3.13.2. Element-level provenance (target)**

| Element | Requirement |
|---|---|
| Conceptual entities / relationships | Optional fields: `firstSeenAt`, `lastValidatedAt`, `source` (`baseline` \| `llm` \| `human`) |
| Mapping entries | Same lineage fields where useful for auditing COLLECTION vs LABEL decisions |

**3.13.3. Change detection and diff (target)**

Schema-analyzer distinguishes between **physical shape** changes (which invalidate the conceptual schema and physical mapping) and **data-volume** changes (which invalidate only derived statistics). Consumers MUST be able to determine which, if either, has occurred **without** running a full snapshot.

| Capability | Requirement |
|---|---|
| **Shape fingerprint** | Cheap `fingerprint_physical_shape(db)` probe — hashes only the collection set, per-collection type, and per-collection sorted index digests `(type, fields, unique, sparse, vci, deduplicate)` with auto-generated `name` and `id` excluded. Stable under ordinary writes. |
| **Counts fingerprint** | Cheap `fingerprint_physical_counts(db)` probe — shape fingerprint concatenated with per-collection `count()`. Changes whenever either the shape or any collection's row count changes. |
| **Change-state contract** | Callers comparing current fingerprints against cached fingerprints MUST be able to derive a four-valued status: `unchanged` (both match), `stats_changed` (shape matches, counts differ), `shape_changed` (shape differs), `no_cache` (no prior fingerprint recorded). |
| **Stats-only refresh** | When status is `stats_changed`, the library MUST preserve the cached `conceptual_schema` and `physical_mapping` and recompute only derived statistics (cf. §3 `statistics` block). Analyzer invocation, OWL regeneration, type-discriminator detection, and sample extraction MUST be skipped on this path. |
| **Trigger re-analysis** | When status is `shape_changed`, flag prior analysis as **stale** or auto-queue re-run (product policy). |
| **Diff** | Compare two `AnalysisResult` payloads (or OWL exports): added/removed/changed entities, relationships, and mapping styles — analogous to AOE `get_ontology_diff` but scoped to **schema-derived conceptual models**. |

**Implementation notes (non-normative):**

- The existing `fingerprint_physical_schema(snapshot)` (§4.1) remains the correct key for a full-snapshot cache. The new shape and counts fingerprints are cheap probes intended for "is refreshing worth it?" decisions, not replacements.
- Auto-generated index identifiers (`name`, `id`) MUST NOT contribute to the shape fingerprint; ArangoDB may assign different values to semantically-equivalent indexes across restarts.
- Transient failures on individual collections (e.g. `indexes()` or `count()` raises) MUST degrade gracefully — the fingerprint function contributes a sentinel rather than propagating the exception.
- Both probes accept an optional `exclude_collections` iterable so callers using a database-resident cache can self-exclude their bookkeeping collection and avoid fingerprint self-perturbation.

**3.13.4. Full temporal graph (optional / long-term)**

Full **edge-interval time travel** for every conceptual entity (AOE-style `created`/`expired` on all versions) is **not** required for v0.1. If schema-analyzer outputs are **imported into AOE**, AOE’s temporal layer can own fine-grained history; this PRD still requires **run-level** and **fingerprint-level** provenance here so handoffs are auditable.

---

### **4. Non-Functional Requirements**

#### **4.1. Caching**

- Default cache is filesystem-based, keyed by physical schema fingerprint (SHA-256 of normalized snapshot).
- The cache interface (`get` / `set` / `invalidate`) is storage-agnostic; deployments MAY substitute alternate backends (collection-backed, Redis, object store, etc.). Any substitute MUST be tolerant to missing entries, corrupt documents, and stale cache-document schema versions.
- When using a database-resident cache (e.g. an ArangoDB collection in the same database being analyzed), the cache collection MUST be excluded from shape-fingerprint computation (`fingerprint_physical_shape(db, exclude_collections={...})`) to prevent self-invalidation on its own writes.
- Cache documents SHOULD carry a `cache_schema_version` field so loading code can refuse-and-discard documents whose shape it no longer understands.
- Configurable TTL (default 86400s / 24h).
- `generated_at` timestamp excluded from fingerprint for stability.

**Implementation**: `cache.py` (`AnalysisCache` / `FilesystemCache`). Alternate backends, if any, live alongside.

#### **4.2. Determinism**

- All outputs (snapshots, fingerprints, JSON) are deterministically ordered
- `stable_dumps()` with `sort_keys=True` ensures reproducibility
- Collection iteration is sorted alphabetically

#### **4.3. Security**

- API keys accepted via env vars (`passwordEnvVar`, `apiKeyEnvVar`) — preferred over inline secrets
- Secrets are never logged or persisted by the library
- AQL fragments use bind parameters exclusively — no string interpolation of collection names
- **Trust boundary — LLM:** When a provider is configured, **physical schema snapshots** (and optional samples) may be transmitted to the vendor API. The PRD treats this as **customer-configured data egress**; documentation and the tool contract must make that explicit. Redaction modes (strip samples, mask field names) are a future hardening item.
- **Untrusted structured output:** Conceptual and mapping payloads may originate from an LLM. Downstream use (e.g. AQL helpers) assumes **validated** mapping shapes; stricter validation (allowlists for collection names and attribute keys from the snapshot) is encouraged where security goals exceed “best effort.”
- **Filesystem cache:** Cached analysis may contain sensitive schema metadata; deployments should restrict cache directory permissions and disk encryption as appropriate.

#### **4.4. Error Handling**

- All domain errors use `SchemaAnalyzerError` with typed `code` field
- Tool entrypoint catches both `SchemaAnalyzerError` and unexpected `Exception`, returning contract-shaped error responses
- LLM workflow retries transient `PROVIDER_ERROR` failures with exponential backoff

#### **4.5. Configuration**

Tunable defaults are centralized in `defaults.py`:

| Parameter | Default | Description |
|---|---|---|
| `MAX_REPAIR_ATTEMPTS` | 2 | LLM output validation repair attempts |
| `MAX_RETRIES` | 2 | Transient provider error retries |
| `RETRY_BASE_DELAY` | 1.0s | Exponential backoff base |
| `LLM_TEMPERATURE` | 0.0 | Sampling temperature for all providers |
| `ANTHROPIC_MAX_TOKENS` | 4096 | Max tokens for Anthropic/OpenRouter |
| `DEFAULT_TIMEOUT_MS` | 60000 | Analysis timeout |
| `DEFAULT_REVIEW_THRESHOLD` | 0.6 | Confidence below this triggers `review_required` |
| `DEFAULT_CACHE_TTL_SECONDS` | 86400 | Cache time-to-live |
| `SAMPLE_VALUE_TOP_K` | 20 | Max distinct values per candidate type field |

#### **4.6. Testing**

- Unit tests with 65%+ coverage threshold
- Integration tests (opt-in via `RUN_INTEGRATION=1`) against Docker ArangoDB
- Golden snapshot tests for determinism validation
- CI: lint (ruff + mypy), test matrix (Python 3.10–3.12), integration on PRs

#### **4.7. Tool contract fidelity**

Fields in `docs/tool-contract/v1/request.schema.json` **must either be implemented** in `tool.py` / `AgenticSchemaAnalyzer` **or be explicitly marked deferred** in this PRD and in schema descriptions. Implemented: **`connection.verifyTls`** (maps to python-arango `verify_override`), **`analysisOptions.maxRepairAttempts`**, **`llm.systemPrompt`**, **`llm.promptVersion`** (participates in LLM cache key with the effective system prompt). Still deferred / future: **`domainContext`** and richer redaction modes. Drift between schema and code undermines agent workflows that rely on the contract.

---

### **5. Architecture**

```
┌─────────────────────────────────────────────────┐
│          CLI / Tool API  │  MCP adapter (opt.)   │
│       (cli.py / tool.py) │  (future, §3.11)      │
├─────────────────────────────────────────────────┤
│              AgenticSchemaAnalyzer               │
│                (analyzer.py)                     │
├──────────┬──────────────┬───────────────────────┤
│ Snapshot │   Workflow   │   Baseline Inference   │
│(snapshot)│  (workflow)  │     (baseline.py)      │
├──────────┤              ├───────────────────────┤
│          │  Providers   │                       │
│          │  (providers/)│    Validation         │
│          │              │   (validation.py)     │
├──────────┴──────────────┴───────────────────────┤
│   ConceptualSchema │ PhysicalMapping │ Cache    │
│   (conceptual.py)  │  (mapping.py)  │(cache.py)│
├─────────────────────────────────────────────────┤
│          Exports: docs / export / owl           │
│     (docs.py / exports.py / owl_export.py)      │
└─────────────────────────────────────────────────┘
```

---

### **6. Roadmap (Post v0.1)**

#### **6.1. Additional Mapping Styles**
- `TRIPLE` — for RDF-style schemas with `_triples` collections and `rdf:type` edges
- `VCI` — for Vertex-Centric Index optimization patterns (denormalized properties on edges)

#### **6.2. Enhanced Pattern Detection**
- RPT (RDF Topology) detection: `_triples` collections, `rdf:type` edges
- GraphRAG template matching: text chunks + entities + similarity edges
- **Vertex-Centric Index (VCI) detection.** Identify VCI usage on edge
  collections and surface it as a first-class signal on the physical
  mapping (beyond today's per-index `vci=true` flag). Two complementary
  signals:
  - **Index-level:** edge collections carrying `persistent` indexes
    rooted at `_from` / `_to` plus one or more discriminator fields
    (e.g. `[_from, type]`, `[_to, type, validFrom]`). Report which
    fields participate, whether the index is `unique` / `sparse`, and
    classify the access pattern (`out-edge`, `in-edge`, `both`).
  - **Schema-level:** edge attributes that duplicate properties from
    their endpoint vertices (denormalisation for VCI lookups). Report
    the duplicated field, source vertex collection, and duplication
    fraction. Emit the candidate `VCI` mapping style alongside the
    underlying `DEDICATED_COLLECTION` / `GENERIC_WITH_TYPE` mapping
    so consumers (transpilers, planners) can choose the optimised
    traversal.
- **Sharding-pattern detection.** Inspect `db.properties()`,
  per-collection `properties()` (`numberOfShards`, `shardKeys`,
  `shardingStrategy`, `replicationFactor`,
  `distributeShardsLike`, `smartGraphAttribute`, `isSmart`,
  `isDisjoint`, `isSatellite`), and named-graph metadata to classify the
  deployment style and emit a top-level `metadata.shardingProfile`:
  - **`OneShard`** — database-level `sharding == "single"` (or every
    user collection has `numberOfShards == 1` and shares the same
    `distributeShardsLike` leader). Report the leader collection.
  - **`SmartGraph`** — named graph with `isSmart == true` and a
    non-empty `smartGraphAttribute`; vertex collections share the
    smart attribute as their shard key. Report the smart attribute,
    member vertex/edge collections, and the named graph(s) that
    define the smart family.
  - **`DisjointSmartGraph`** — SmartGraph with `isDisjoint == true`
    (tenant-sharding pattern: cross-tenant traversal is forbidden by
    construction). Report the disjoint shard key in addition to the
    smart attribute, and flag the graph as a tenancy boundary so
    multitenancy detection (below) can consume it.
  - **`SatelliteGraph`** — vertex/edge collections with
    `replicationFactor == "satellite"` (or `isSatellite == true`),
    typically used for meta-graph / ontology / reference data that
    must be co-located with every shard. Report which collections are
    satellites and which named graphs reference them.
  - **`Sharded` (default)** — none of the above; standard
    hash-sharded collections. Report `shardKeys` and
    `numberOfShards` per collection.
  Detection must be read-only and must not require admin privileges
  beyond what `snapshot_physical_schema` already needs; missing fields
  (older ArangoDB versions, restricted users) degrade to `Sharded`
  with a `metadata.shardingProfileStatus` of `"degraded"` and a
  human-readable reason.
- **Multitenancy detection.** Determine whether the physical schema
  encodes multitenancy and, if so, how. Emit
  `metadata.multitenancy` with:
  - `style` ∈ {`none`, `disjoint_smartgraph`, `shard_key`,
    `discriminator_field`, `collection_per_tenant`,
    `database_per_tenant`}
  - `tenantKey` — the property name(s) that identify a tenant
    (e.g. `tenantId`, `org_id`, `accountId`)
  - `tenantKeyCollections` — collections in which the tenant key
    appears, with coverage (`fraction` of documents that carry the
    key) and cardinality (distinct tenant value count, capped)
  - `physicalEnforcement` — whether tenancy is enforced by the
    physical layout (`disjoint_smartgraph`, `shard_key`,
    `collection_per_tenant`, `database_per_tenant`) or only by
    convention (`discriminator_field`)
  - `evidence` — the signals that drove the classification
    (e.g. shard-key match, smart-attribute match, naming pattern,
    high-coverage discriminator)
  Detection layers on the sharding profile above:
  - **Disjoint SmartGraph multitenancy.** When
    `shardingProfile.style == "DisjointSmartGraph"`, the smart /
    disjoint attribute is the tenant key and `physicalEnforcement` is
    `true`. This is the canonical ArangoDB tenant-sharding pattern.
  - **Shard-key multitenancy.** When non-disjoint collections share a
    `shardKeys` value such as `["tenantId"]` (or
    `distributeShardsLike` follows a leader that does), report the
    shard key as the tenant key with `physicalEnforcement = true`.
  - **Discriminator-field multitenancy.** When a property such as
    `tenantId` / `org_id` / `accountId` appears with high coverage
    (`≥ MIN_TENANT_FIELD_COVERAGE_FRACTION`) across many collections
    but is not a shard key, classify as `discriminator_field` with
    `physicalEnforcement = false` and warn that tenancy is convention
    only.
  - **Collection-per-tenant.** When collection naming follows a
    repeated `<base>__<tenant>` / `<tenant>_<base>` pattern across a
    consistent set of bases, report the inferred base set, the
    extracted tenant identifiers, and `physicalEnforcement = true`.
  - **Database-per-tenant.** Out of scope for a single-database
    snapshot, but the analyzer should mark the result as
    `style: "unknown_single_db"` when the snapshot scope is a single
    database that itself looks tenant-named (e.g. matches a known
    tenant naming pattern), so a higher-level orchestrator can
    aggregate across databases.
  Tunables (e.g. `MIN_TENANT_FIELD_COVERAGE_FRACTION`,
  `MAX_TENANT_DISTINCT_VALUES`) live in `defaults.py`. Detection must
  be deterministic and must not depend on LLM output, though the LLM
  layer may enrich the human-readable description.
- **Shard-family detection.** Some schemas encode a per-source /
  per-repository / per-stream dimension by duplicating structurally
  identical collections keyed on that dimension (e.g.
  `IBEX_Documents`, `MAROCCHINO_Documents`, `MOR1KX_Documents`,
  `OR1200_Documents` — four collections, one logical entity from a
  consumer's perspective). Emit
  `physicalMapping.shardFamilies[]` grouping these together:
  - `name` — family label derived from the common suffix (e.g.
    `"Document"`).
  - `suffix` — the common name suffix (`"Document"`,
    `"_Golden_Entities"`, …), ≥ `MIN_SHARD_FAMILY_SUFFIX_LEN`
    characters, ending on a capital-letter boundary.
  - `members[]` — one entry per member with
    `{entity, collectionName, discriminatorValue}`.
  - `sharedProperties[]` — the property set common to all members.
  - `discriminator` — `{source: "field", field: "<name>"}` when a
    matching field (default `repo`, configurable via
    `SHARD_FAMILY_DISCRIMINATOR_FIELDS`) exists on the member
    collections; `{source: "collection_prefix"}` otherwise.

  Detection is deterministic: bucket conceptual entities by the hash
  of their sorted property names; within each bucket, confirm a
  common suffix of at least `MIN_SHARD_FAMILY_SUFFIX_LEN`. Buckets of
  size < `MIN_SHARD_FAMILY_SIZE` are skipped. This is explicitly
  **not** multi-tenancy (structural duplication, not per-customer
  isolation) and layers independently of `metadata.multitenancy`.
  Downstream consumers (e.g. NL→Cypher prompt builders) use the
  family grouping to emit UNION-aware guidance instead of silently
  picking one member. Tunables
  (`MIN_SHARD_FAMILY_SIZE`,
  `MIN_SHARD_FAMILY_SUFFIX_LEN`,
  `SHARD_FAMILY_DISCRIMINATOR_FIELDS`) live in `defaults.py`.

#### **6.3. Richer OWL Support**
- Class hierarchies (`rdfs:subClassOf`)
- Property characteristics (`owl:functional`, `owl:inverseOf`)
- Cardinality constraints
- JSON-LD export format alongside Turtle
- Integration with OWL reasoners for consistency checking

#### **6.4. Transpiler Integration**
- Direct integration with `arango-cypher` to consume mapping output
- SPARQL query generation from OWL conceptual schema
- SQL mapping for relational query translation

#### **6.5. Advanced Features**
- **Schema evolution and lineage** — See §3.13 (run records, fingerprint linkage, diff between analyses, stale detection). Optional alignment with AOE temporal imports when analyses are promoted into `ontology_generator`.
- **Quality metrics expansion** — See §3.12.3 (structural ontology metrics, optional gold recall, health score, metric history).
- Confidence calibration from eval feedback loops
- Streaming/incremental analysis for large databases
- Custom provider SDK support beyond OpenAI/Anthropic/OpenRouter
- **MCP packaging** — Standalone MCP server or module as in §3.11

---

### **7. Dependencies**

#### Core
- `python-arango ≥ 8.1.1` — ArangoDB client
- `pydantic ≥ 2.6.0` — data validation and metadata models
- `jsonschema ≥ 4.21.0` — tool contract validation

#### Optional
- `openai ≥ 1.0.0` — OpenAI provider
- `anthropic ≥ 0.25.0` — Anthropic provider

#### Dev
- `pytest`, `pytest-cov`, `ruff`, `mypy`
