# Downstream migration note — `arango-cypher-py`

**Audience:** maintainers of `arango-cypher-py`
**Upstream repo:** `ArthurKeen/arango-schema-mapper` (package: `arangodb-schema-analyzer`)
**Scope:** what changed in v0.2.0 and the current `main` (post-v0.2.0), and what to
delete / update on the `arango-cypher-py` side to pick it up.

File this as a GitHub issue on `arango-cypher-py` (or paste into your handoff doc)
so the migration shows up in your backlog.

---

## TL;DR

Six upstream issues closed in two PRs:

| Upstream | Issue | PR | Status |
|---|---|---|---|
| #2 | Emit `vci` / `deduplicate` / `storedValues` on physical-mapping indexes | #9 | shipped in v0.2.0 |
| #3 | Emit a `statistics` block with per-relationship cardinality and selectivity | #9 | shipped in v0.2.0 |
| #4 | Detect multi-type edge collections and emit per-type `GENERIC_WITH_TYPE` entries | #9 | shipped in v0.2.0 |
| #5 | Guarantee every snapshot collection is represented in the exported mapping | #9 | shipped in v0.2.0 |
| #6 | Hard-rename `physicalFieldName`→`field`; drop `collectionName` on relationships | #9 | **breaking**, shipped in v0.2.0 |
| #7 | Cheap db-keyed `fingerprint_physical_shape(db)` + `fingerprint_physical_counts(db)` probes | #10 | on `main`, will ship in the next release |

If you've been running a normalization shim or a local copy of the shape-fingerprint
code, this release lets you delete both.

---

## Action items for `arango-cypher-py`

### 1. Pin the upstream version

Once v0.2.0 is published to PyPI:

```toml
# pyproject.toml
dependencies = [
    "arangodb-schema-analyzer>=0.2.0",
    # ...
]
```

If you want the cheap fingerprint probes today, depend on the git SHA / `main` until
a `0.3.0` (or `0.2.1`) is cut — the probes are on `main` but not yet tagged:

```toml
"arangodb-schema-analyzer @ git+https://github.com/ArthurKeen/arango-schema-mapper@main",
```

### 2. Delete the local key-normalization shim (#6)

Upstream now emits the canonical keys directly, so any wrapper that was rewriting
the analyzer's output can go away.

**Delete:** `_normalize_analyzer_pm`, `_normalize_props`, or equivalently named
helpers that were mapping `physicalFieldName → field` and/or
`collectionName → edgeCollectionName` on relationships.

**What the analyzer now emits:**

```jsonc
// Entity property mapping
{ "name": "email", "field": "email", "indexed": true }

// Relationship mapping — canonical keys
{
  "type": "ACTED_IN",
  "physicalModelStyle": "DEDICATED_COLLECTION",
  "edgeCollectionName": "acted_in",
  "properties": [ ... ]
}
```

Note the asymmetry (intentional):

- Entities still use `collectionName` (unchanged).
- **Relationships use `edgeCollectionName`** and the JSON schema now **rejects**
  `collectionName` on relationships. Any transformer that still emits
  `collectionName` for a relationship will hit validation failure against
  `tool_contract/v1/response.schema.json`.

### 3. Replace the local shape-fingerprint implementation (#7)

Per the body of issue #7, `arango-cypher-py` had a local implementation in
`arango_cypher/schema_acquire.py`:

- `_shape_fingerprint(db, exclude_collections=...)`
- `_full_fingerprint(db, exclude_collections=...)`
- `_index_digest(idx)`
- `_iter_user_collections(db, exclude_collections=...)`

**Delete all four** (~60 LOC) and import the upstream versions:

```python
from schema_analyzer import (
    fingerprint_physical_shape,
    fingerprint_physical_counts,
)

# At the top of schema_acquire.py (or wherever the cache key is built):
shape_fp  = fingerprint_physical_shape(db, exclude_collections={DEFAULT_CACHE_COLLECTION})
counts_fp = fingerprint_physical_counts(db, exclude_collections={DEFAULT_CACHE_COLLECTION})
```

**Semantic compatibility** — the upstream helpers match the `arango-cypher-py`
behavior on every acceptance criterion in issue #7:

- System collections (`_`-prefixed) excluded.
- Auto-generated index `name` / `id` excluded from the digest.
- Per-index digest fields: `(type, fields, unique, sparse, vci, deduplicate)`.
- Primary indexes contribute no bytes.
- Stable under ordinary INSERT / UPDATE / REMOVE writes.
- Per-collection failures degrade to a sentinel rather than raising.
- `exclude_collections` supported on both helpers.

The raw hex digests **will differ** from your local implementation (different
delimiter / prefix bytes), so on first upgrade every consumer will see one
`shape_changed` event and rebuild its cache once. That's expected and one-time.

**Do not** keep both side-by-side in prod — if the raw hex digest must stay
stable across the upgrade (e.g. you have cached documents keyed on the local
digest), migrate the stored keys lazily: check under both old and new keys on
read, write only the new key.

Your `SchemaChangeReport` logic is unchanged. The four-state contract
(`unchanged` / `stats_changed` / `shape_changed` / `no_cache`) that
`arango-cypher-py` already derives from the two fingerprints is now formally
sanctioned in upstream PRD §3.13.3 (see #8).

### 4. Consume the new `metadata.statistics` block (#3) — optional

`AgenticSchemaAnalyzer` now stamps `metadata.statistics` on the response:

```jsonc
{
  "metadata": {
    "statistics_status": "ok",
    "statistics": {
      "collections": { "<name>": { "count": 12345 } },
      "entities":    { "<Entity>": { "estimated_count": 12345 } },
      "relationships": {
        "<Type>": {
          "edge_count":      54321,
          "source_count":    12000,
          "target_count":    12345,
          "avg_out_degree":  4.53,
          "avg_in_degree":   4.41,
          "cardinality_pattern": "N:M",
          "selectivity":     0.98
        }
      }
    }
  }
}
```

When no live DB is available `statistics_status = "skipped_no_db"` and
`statistics` is absent. If `arango-cypher-py` used to compute these
server-side, you can now drop that code and consume the upstream values
(or still compute them — the blocks are semantically identical).

### 5. Expect per-type `GENERIC_WITH_TYPE` entries on shared edge collections (#4)

For shared edge collections with a detected discriminator, upstream now emits
**one mapping entry per distinct `typeValue`** instead of a single grouped
entry. Each entry carries the same `edgeCollectionName`, same `typeField`,
and a distinct `typeValue`.

If `arango-cypher-py` was post-processing to split grouped entries by
discriminator, delete that splitter — the analyzer now does it.

If you were collapsing by `(collection, typeField)` on the consumer side,
you'll want to re-check whether you actually want that — each per-type entry
can legitimately carry different `fromEntity` / `toEntity` endpoints.

### 6. Expect every snapshot collection in the output (#5)

The analyzer now runs a reconciliation pass after the LLM: any collection
present in the snapshot but missing from the LLM's mapping is backfilled via
baseline inference. The merge is reported in `metadata.reconciliation`:

```jsonc
{
  "metadata": {
    "reconciliation": {
      "llm_covered_collections":  [...],
      "snapshot_collections":     [...],
      "backfilled_collections":   [...],
      "strategy": "baseline_backfill"
    },
    "warnings": [ "LLM output omitted collections X, Y; backfilled via baseline." ]
  }
}
```

If `arango-cypher-py` was rejecting LLM responses with incomplete coverage,
you can relax that check — upstream guarantees coverage now. If you were
running your own backfill, delete it.

### 7. Leverage richer index metadata (#2)

`physicalMapping[...].indexes[*]` entries now include:

- `vci` (vertex-centric index flag)
- `deduplicate` (set when the raw metadata indicates it)
- `storedValues` (when present)

Upstream does **not** consider a VCI hit as "indexed=True" on the property
heuristic — that matches what `arango-cypher-py` was doing locally, so no
change needed unless you want to read the new fields.

---

## What you do **not** need to change

- `tool_contract/v1/request.schema.json` — not modified.
- Cache key derivation — `fingerprint_physical_schema(snapshot)` retains its
  existing semantics. Only the new **cheap** probes are additive.
- The `analyze` / `run_tool` entrypoints are unchanged.
- Entity `collectionName` is unchanged (only the **relationship** naming
  changed in #6).

---

## Suggested rollout

1. Wait for v0.2.0 to land on PyPI, or pin to a main-SHA temporarily.
2. In a single branch:
   - bump the `arangodb-schema-analyzer` dependency pin,
   - delete the key-normalization shim (#6),
   - delete the local shape-fingerprint helpers (#7),
   - drop any per-type edge splitter (#4) and LLM-coverage checker (#5) if
     they existed,
   - optionally adopt the `metadata.statistics` block (#3) and new index
     flags (#2).
3. Run your full suite including `test_schema_change_detection.py` /
   `TestSchemaFingerprints` — they should stay green modulo the one-time
   hex-digest change noted above.
4. Expect exactly one `shape_changed` event in prod on first deploy after
   the upgrade as caches re-key on the new digest format, then steady-state.

---

## References

- Upstream repo: https://github.com/ArthurKeen/arango-schema-mapper
- Release notes: `CHANGELOG.md` (`0.2.0` and `Unreleased`)
- PRs:
  - https://github.com/ArthurKeen/arango-schema-mapper/pull/9 (#2, #3, #4, #5, #6)
  - https://github.com/ArthurKeen/arango-schema-mapper/pull/10 (#7, addresses #8)
- PRD sections of interest: §3.13.3 (change-detection contract), §4.1 (caching)
