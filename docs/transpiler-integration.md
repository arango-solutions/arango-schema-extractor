# Transpiler integration guide

This library does **not** translate Cypher/SPARQL/SQL queries to AQL. It produces
a stable **mapping contract** that a transpiler consumes to do that translation
itself. This document is the contract for transpiler authors: what the export
shapes contain and how to turn each into AQL.

Two entry points, both stable and JSON-serializable:

| Export | Function | v1 operation | For |
|---|---|---|---|
| Cypher resolution index | `build_cypher_resolution_index(analysis)` | `resolve` | Cypher → AQL |
| SPARQL vocabulary view | `export_mapping(analysis, target="sparql")` | `export` (`exportTarget=sparql`) | SPARQL → AQL |
| Raw bundle | `export_mapping(analysis, target="cypher")` | `export` (`exportTarget=cypher`) | anything (raw conceptual + mapping) |

All AQL fragments are **injection-safe**: collection and discriminator values are
passed as bind variables, never string-interpolated. Never reconstruct AQL by
concatenating names yourself — use the provided `query` + `bindVars`.

---

## Cypher resolution index (`resolve`)

```jsonc
{
  "target": "cypher",
  "entities": {
    "User": {
      "style": "COLLECTION", "collectionName": "users",
      "aql": { "query": "FOR n IN @@col_n", "bindVars": { "@col_n": "users" } }
    },
    "Post": {
      "style": "LABEL", "collectionName": "nodes", "typeField": "type", "typeValue": "Post",
      "aql": { "query": "FOR n IN @@col_n FILTER n.@tf_n == @tv_n",
               "bindVars": { "@col_n": "nodes", "tf_n": "type", "tv_n": "Post" } }
    }
  },
  "relationships": {
    "WROTE": {
      "style": "DEDICATED_COLLECTION", "edgeCollectionName": "wrote",
      "aql": { "query": "FOR b, r IN 1..1 OUTBOUND a @@edge_r", "bindVars": { "@edge_r": "wrote" },
               "edgeVariable": "r" }
    }
  }
}
```

**How to consume:**

1. **Node pattern** `(n:User)` → look up `entities["User"].aql`. Splice its `query`
   as the `FOR` clause and merge its `bindVars`. For a `LABEL`-style entity the
   query already includes the `FILTER n.type == "Post"` discriminator.
2. **Relationship pattern** `(a)-[r:WROTE]->(b)` → look up
   `relationships["WROTE"].aql`. It is a traversal rooted at the variable `a`
   (rename to match your plan); `edgeVariable` is the bound edge variable for
   `GENERIC_WITH_TYPE` styles that also emit a `FILTER r.relType == ...`.
3. **Variable renaming:** the fragments use placeholder variables (`n`, `a`, `b`,
   `r`) and **suffix their bind keys with the variable** (`@col_n`, `tf_n`) so two
   fragments compose without bind-key collisions. Rename consistently.

**Incomplete mappings.** When a mapping can't produce AQL (e.g. a `LABEL` entity
missing its `typeValue`), the entry carries an `error` block **instead of** `aql`:

```jsonc
"Comment": { "style": "LABEL", "collectionName": "nodes",
             "error": { "code": "INVALID_ARGUMENT", "message": "LABEL mapping requires typeValue" } }
```

Surface `error.message` as a diagnostic; do not synthesize a fallback query.

---

## SPARQL vocabulary view (`export`, `exportTarget=sparql`)

```jsonc
{
  "target": "sparql",
  "prefixes": { "": "https://.../", "phys": "...", "owl": "...", "rdf": "...", "rdfs": "...", "xsd": "..." },
  "classes": [
    { "iri": ":User", "localName": "User", "label": "User",
      "physical": { "style": "COLLECTION", "collectionName": "users" } }
  ],
  "objectProperties": [
    { "iri": ":wrote", "localName": "wrote", "label": "WROTE",
      "domain": ":User", "range": ":Post",
      "physical": { "style": "DEDICATED_COLLECTION", "edgeCollectionName": "wrote" } }
  ],
  "datatypeProperties": [
    { "iri": ":email", "localName": "email", "label": "email",
      "domain": ":User", "attribute": "email",
      "physical": { "style": "COLLECTION", "collectionName": "users" } }
  ],
  "physicalMapping": { /* full mapping, for anything not pre-resolved */ }
}
```

**How to resolve a triple pattern `?s p ?o`:**

1. **`?s rdf:type :User`** → `classes[].physical`. Iterate the matching
   collection; for a `LABEL` style add the `typeField == typeValue` filter
   (mirrors the Cypher index above).
2. **`?s :wrote ?o`** (predicate in `objectProperties`) → a graph traversal over
   `physical.edgeCollectionName`, from the `domain` class to the `range` class.
   `GENERIC_WITH_TYPE` edges add a `typeField == typeValue` filter on the edge.
3. **`?s :email ?o`** (predicate in `datatypeProperties`) → **not** a traversal:
   it's a document-field access. Read `physical.attribute` (the field name) on the
   `domain` class's collection — `RETURN s.email`, or `FILTER s.email == @v` for a
   bound object. This is the piece that lets literal-valued patterns resolve;
   without it a SPARQL transpiler can only handle entity/relationship patterns.

`domain`/`range`/IRIs are absent only when the source analysis lacks an endpoint;
fall back to `physicalMapping` or report an unresolved pattern. The same datatype
predicate IRI may appear under multiple `domain`s (a property reused across
classes) — resolve by `(domain, predicate)`, not predicate alone.

---

## Named graphs

By default the analyzer covers the **whole database** and labels which ArangoDB
named graph(s) each element belongs to; you can also **scope** an analysis to a
single named graph. Both are additive — absent when the database has no named
graphs, so non-graph schemas are unaffected.

### What you receive

**Per-entry annotation** (on every `physicalMapping` entity/relationship; also
present in CSI's `arangoPhysicalMapping`):

```jsonc
"entities":      { "User":    { "style": "COLLECTION", "collectionName": "users", "graphs": ["content","social"] } },
"relationships": { "FOLLOWS": { "style": "DEDICATED_COLLECTION", "edgeCollectionName": "follows", "graphs": ["social"] } }
```

- `graphs` is sorted and **multi-valued** (a vertex collection shared across
  graphs lists all of them) and is **omitted** when the element is in no named
  graph (an *ungraphed* collection).

**Summary block** `metadata.graphMembership` (present in the `analyze` response;
**not** in a CSI document, which carries only provenance metadata — reconstruct
from per-entry `graphs` if you consume CSI):

```jsonc
{
  "status": "ok",
  "graphCount": 2,
  "graphs": {
    "social":  { "entities": ["User"], "relationships": ["FOLLOWS"],
                 "vertexCollections": ["users"], "edgeCollections": ["follows"] },
    "content": { "entities": ["Post","User"], "relationships": ["WROTE"],
                 "vertexCollections": ["posts","users"], "edgeCollections": ["wrote"] }
  },
  "ungraphed": { "entities": ["Loose"], "relationships": [] }
}
```

**Scoping input** `analysisOptions.graphScope: "<graphName>"` (or
`analyze_physical_schema(graph_scope=...)`): restricts the snapshot/analysis to
that graph's collections (edge collections + their `from`/`to` vertices +
orphans). A missing graph yields `{ ok:false, error:{ code:"INVALID_ARGUMENT" }}`.

### How to consume

**Cypher → AQL:**

- When a relationship's mapping has `graphs: ["<name>"]`, you may emit a
  **named-graph traversal** (`FOR v,e IN OUTBOUND start GRAPH "<name>"`) instead
  of the edge-collection form. When the relationship is **ungraphed** (no
  `graphs`), keep the collection-based traversal — `GRAPH "<name>"` is not
  available for it.
- A label whose entity is shared across graphs has `graphs: [g1, g2]`;
  disambiguate by the relationship(s) in the query, or surface the choice — do
  not assume one.

**SPARQL → AQL:**

- Map SPARQL named graphs to ArangoDB named graphs directly:
  `GRAPH :social { ?a :FOLLOWS ?b }` → restrict to
  `graphMembership.graphs["social"]`'s vertex/edge collections (or `GRAPH
  "social"`). Use the `graphs` keys as the available named-graph IRIs.
- The **default graph** (no `GRAPH` clause) is your dataset policy; **ungraphed**
  collections belong only to the default graph (they are in no named graph).
- Per-class / per-predicate membership comes from the entity/relationship
  `graphs` lists, complementing the IRIs in the SPARQL vocabulary view above.

Either transpiler can request `graphScope` to obtain a focused, single-graph
mapping when a query is meant to run within one named graph.

> **Note (≥ this release):** graph definitions are now parsed correctly from
> live databases (python-arango's normalized `edge_definitions` /
> `edge_collection` / `*_vertex_collections` shape). Earlier builds left
> `graphs_detailed` edge definitions empty against real ArangoDB, so any
> graph-derived signals were unreliable.

---

## Stability & versioning

- The fields above are a contract: keys are added compatibly, not removed or
  renamed within v1. Both export objects set `additionalProperties: true`, so
  tolerate unknown keys.
- The bundled JSON Schemas (`schema_analyzer/tool_contract/v1/response.schema.json`,
  `SparqlExport` / resolution `$defs`) are the machine-readable source of truth and
  are asserted byte-identical to the published copies under `docs/tool-contract/v1/`.
- `style` values are exactly the four mapping styles in PRD §3.3
  (`COLLECTION`, `LABEL`, `DEDICATED_COLLECTION`, `GENERIC_WITH_TYPE`); the
  additive `TRIPLE` / VCI annotations (PRD §6.1) appear alongside, never replacing
  the native `style`.
