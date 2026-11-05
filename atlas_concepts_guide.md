# Apache Atlas: Concepts & API Guide

A hands-on guide to the core concepts of Apache Atlas and its REST APIs, with practical examples you can run against this lab. Every example below was **executed and verified against the running lab** (Atlas 2.1.0, `http://localhost:21000`, `admin`/`admin`), and the responses shown are real (trimmed for readability).

> **How to use this guide:** read a concept section, then run its curl examples in order. Sections build on each other: types → entities → classifications → lineage → glossary → search. No tools needed beyond `curl` and `python3` (for pretty-printing).
>
> **Lab state:** the entities used as examples below already exist in this lab (they were created while validating this guide — see §8). If you re-run a *create* example, Atlas will create a duplicate (there is **no dedup on `qualifiedName`**). To practice from a clean slate: `docker compose down -v && docker compose up -d`, wait for healthy, run `./smoke_test.sh`, then follow along.

---

## Table of contents

1. [What is Apache Atlas?](#1-what-is-apache-atlas)
2. [The big picture](#2-the-big-picture)
3. [Core concepts](#3-core-concepts)
4. [API cookbook](#4-api-cookbook)
5. [Atlas 2.1.0 quirks & gotchas](#5-atlas-210-quirks--gotchas)
6. [v1 vs v2 APIs](#6-v1-vs-v2-apis)
7. [The lab's worked example](#7-the-labs-worked-example)
8. [Suggested exercises](#8-suggested-exercises)

---

## 1. What is Apache Atlas?

Apache Atlas is a **metadata management and data governance platform** for Hadoop-scale ecosystems. In practice it answers questions like:

* *What data do we have?* — a **catalog** of tables, files, reports, models (metadata entities)
* *Where did this data come from?* — **lineage**: which inputs produced which outputs, through which jobs
* *Who owns it, what does it mean?* — owners, descriptions, **glossary** terms
* *Is this data sensitive?* — **classifications** (tags) like `PII`, `SOX`, `CONFIDENTIAL`
* *Who changed what, when?* — **audit** history of every mutation

Atlas is typically populated automatically by **hooks** (Hive, HBase, Spark, Kafka bridges push metadata as things happen) or via its **REST APIs** — which is what this lab exercises. The same APIs the dashboard uses are the ones integration tools use in production.

## 2. The big picture

```
        REST API (v2)                    hooks / bridges
             │                                 │
             ▼                                 ▼
   ┌────────────────────────── Atlas server ─────────────────────────┐
   │  Type system   →  Entities (graph)  →  Search index (Solr)      │
   │       │                  │                     │                │
   │ TypeDefs define     vertices + edges       vertex/edge/fulltext │
   │  the "schema"       in HBase (JanusGraph)  collections          │
   └─────────────────────────────────────────────────────────────────┘
```

Three stores cooperate:

| Store | Holds | You see it via |
|---|---|---|
| **Type system** | Definitions (schemas) — cached in memory, persisted in the graph | Types API |
| **Graph (HBase/JanusGraph)** | Entities as vertices, relationships as edges | Entity/Relationship APIs |
| **Search index (Solr)** | Flattened copies of entities for fast lookup | Search APIs |

Key implication: **writes go to the graph, then to Solr asynchronously** (via Kafka notifications). A just-created entity is immediately fetchable by GUID but can lag a few seconds in search — always in this guide, and in production too.

## 3. Core concepts

### 3.1 The type system — TypeDefs

Everything in Atlas is an instance of a **type**. Types are defined by *TypeDefs*, grouped in five categories:

| Category | Defines | Example |
|---|---|---|
| `entityDefs` | Types of metadata objects (the main one) | `my_custom_table`, `Process`, `AtlasGlossaryTerm` |
| `classificationDefs` | Tags that can be attached to entities | `PII`, `CONFIDENTIAL` |
| `relationshipDefs` | Typed edges between entities | `AtlasGlossarySemanticAssignment` |
| `structDefs` | Compound value types (not entities) | a `TimeDimension` struct |
| `enumDefs` | Enumerations | a `data_tier` enum |

An entity type has **`superTypes`** (inheritance) and **`attributeDefs`** (its schema). The lab's type:

```json
{
  "name": "my_custom_table",
  "superTypes": ["DataSet"],
  "serviceType": "custom_lab",
  "typeVersion": "1.0",
  "attributeDefs": [
    {"name": "owner",     "typeName": "string", "isOptional": true, "cardinality": "SINGLE"},
    {"name": "data_tier", "typeName": "string", "isOptional": true, "cardinality": "SINGLE"}
  ]
}
```

Because `my_custom_table` extends **`DataSet`** (which extends `Referenceable`/`Asset`), it inherits crucial attributes without defining them: `name`, **`qualifiedName`**, `description`, plus relationship attributes like `inputToProcesses`/`outputFromProcesses` (used for lineage).

Atlas ships with a rich built-in type hierarchy (`DataSet`, `Process`, `Infrastructure`, `AtlasGlossary`, `AtlasGlossaryTerm`, ...). You rarely start from scratch — you extend.

### 3.2 Entities — instances with GUIDs

An **entity** is an instance of a type: one vertex in the graph. Every entity gets a **GUID** (assigned by Atlas, immutable, your primary key for all operations) and a **status** (`ACTIVE` or `DELETED`).

Two kinds of attributes on an entity:

* **`attributes`** — the type's own schema fields (`name`, `owner`, `data_tier`, ...)
* **`relationshipAttributes`** — edges to other entities, materialized for you on read (e.g. `meanings`, `inputToProcesses`). They are *derived from relationships*, not stored as plain attributes.

### 3.3 `qualifiedName` — the human key

`qualifiedName` is the **unique attribute** inherited from `Referenceable`: a stable, human-readable identity like `raw_zone.orders_raw@lab_cluster` (convention: `database.table@cluster`). You can look entities up by it without knowing the GUID. But note: **Atlas does not enforce uniqueness** — posting the same `qualifiedName` twice creates two entities. Treat it as a convention your tooling must respect (the `smoke_test.sh` check-before-create pattern).

### 3.4 Classifications — governance tags

A **classification** is an instance of a `classificationDef` attached to entities. Classifications can carry their own attributes (e.g. `PII` with `sensitivity="high"`), and they **propagate** along lineage by default: tag a raw table `PII` and every dataset derived from it inherits the tag (visible as `propagatedClassifications`). This is the backbone of governance policies — e.g. "no `PII` data may leave the raw zone."

### 3.5 Lineage — Process entities and relationships

Atlas models data flow as **`Process` entities** (an entity type, like a job or query) whose `inputs` and `outputs` reference datasets. Each input/output becomes a typed **edge** (`dataset_process_inputs` / `dataset_process_outputs`). The **lineage API** then walks those edges to answer "where did this come from / where does it go". Lineage is not a separate store — it is just the graph.

### 3.6 Glossary — business vocabulary

A **glossary** (`AtlasGlossary`) contains **terms** (`AtlasGlossaryTerm`). A term is linked to the entities it describes via the `AtlasGlossarySemanticAssignment` relationship (term side: `assignedEntities`; entity side: `meanings`). In Atlas 2.1.0 glossaries and terms are **plain entities** — you manage them with the regular entity and relationship APIs (see §4.7 for the version-specific details).

### 3.7 Search — three query languages

| API | Query style | Example |
|---|---|---|
| `basic` | attribute-ish filters via params | `?typeName=my_custom_table&classification=PII&query=orders` |
| `dsl` | Atlas DSL — type-oriented query language | `from DataSet where data_tier = "Raw"` |
| `fulltext` | free text over a dedicated index | `?query=orders` |

All are Solr-backed. `basic` is what the dashboard and most integrations use; `dsl` is the most expressive. **In 2.1.0 only `from`/`where` work reliably in DSL** — the `isa` (classification) and `has` operators return empty results; filter classifications with basic search's `classification` param instead. Note: **basic search returns soft-deleted entities unless you pass `excludeDeletedEntities=true`**.

### 3.8 Mutations, soft delete, audit

Every create/update/delete is recorded as an **audit event** (who, when, before/after). `DELETE` is a **soft delete**: the entity stays in the graph with `status: DELETED` (and still shows in basic search!). This is deliberate — governance needs history. In 2.1.0 there is no REST hard-delete (see §5).

---

## 4. API cookbook

All examples: `A=http://localhost:21000`, auth `admin:admin`. Run them from the project folder.

### 4.1 Health check

Atlas 2.1.0 has **no v2 admin endpoints** — this is v1 only:

```bash
curl -s -u admin:admin "$A/api/atlas/admin/status"
```

```json
{"Status":"ACTIVE"}
```

(Mind the capital `S` — a classic trap when parsing.)

### 4.2 Types API

**Fetch one typedef:**

```bash
curl -s -u admin:admin "$A/api/atlas/v2/types/typedef/name/my_custom_table" \
  | python3 -m json.tool | head -20
```

```json
{
  "category": "ENTITY",
  "guid": "2a984147-e5e4-402b-9a2f-b2e902ba1339",
  "createdBy": "admin",
  "name": "my_custom_table",
  "typeVersion": "1.0",
  "serviceType": "custom_lab",
  "superTypes": ["DataSet"],
  "attributeDefs": [
    { "name": "owner", "typeName": "string", "isOptional": true, "cardinality": "SINGLE" },
    { "name": "data_tier", "typeName": "string", "isOptional": true, "cardinality": "SINGLE" }
  ]
}
```

**Register new types** (any mix of categories in one call):

```bash
curl -s -u admin:admin -X POST -H "Content-Type: application/json" \
  -d @- "$A/api/atlas/v2/types/typedefs" <<'EOF'
{
  "classificationDefs": [
    {
      "name": "PII",
      "serviceType": "custom_lab",
      "typeVersion": "1.0",
      "description": "Personally identifiable information",
      "attributeDefs": [
        {"name": "sensitivity", "typeName": "string", "isOptional": true, "cardinality": "SINGLE"}
      ]
    }
  ]
}
EOF
```

The response echoes the registered definitions per category (`enumDefs`, `structDefs`, `classificationDefs`, ...). Registering an existing name fails with **HTTP 409**:

```json
{"errorCode":"ATLAS-409-00-001","errorMessage":"Given type PII already exists"}
```

**List everything:** `GET /api/atlas/v2/types/typedefs` returns *all* definitions (large). Filter client-side:

```bash
curl -s -u admin:admin "$A/api/atlas/v2/types/typedefs" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print([r['name'] for r in d['relationshipDefs'] if 'Glossary' in r['name']])"
```

**Delete a typedef:** `DELETE /api/atlas/v2/types/typedef/name/{name}` — only works when nothing references the type (entities of that type, even deleted ones, block it: `ATLAS-409-00-002 "has references"`).

### 4.3 Entity API

**Create** — `POST /api/atlas/v2/entity`:

```bash
curl -s -u admin:admin -X POST -H "Content-Type: application/json" \
  -d @- "$A/api/atlas/v2/entity" <<'EOF'
{
  "entity": {
    "typeName": "my_custom_table",
    "attributes": {
      "name": "customers_raw",
      "qualifiedName": "raw_zone.customers_raw@lab_cluster",
      "description": "Raw ingestion table for customer records",
      "owner": "DataEngineeringTeam",
      "data_tier": "Raw"
    }
  }
}
EOF
```

Response (trimmed) — note where the new GUID appears:

```json
{
  "mutatedEntities": {
    "CREATE": [
      {
        "typeName": "my_custom_table",
        "attributes": { "qualifiedName": "raw_zone.customers_raw@lab_cluster", "name": "customers_raw", ... },
        "guid": "822d3513-f2ed-48c4-a6d7-047cbcad94ed",
        "status": "ACTIVE",
        "displayText": "customers_raw"
      }
    ]
  },
  "guidAssignments": { "-3447532577313": "822d3513-f2ed-48c4-a6d7-047cbcad94ed" }
}
```

(`guidAssignments` maps the temporary negative id from your request to the real GUID — handy when you post several entities at once.)

**Read by GUID:**

```bash
curl -s -u admin:admin "$A/api/atlas/v2/entity/guid/822d3513-f2ed-48c4-a6d7-047cbcad94ed"
```

```json
{
  "entity": {
    "guid": "822d3513-f2ed-48c4-a6d7-047cbcad94ed",
    "typeName": "my_custom_table",
    "status": "ACTIVE",
    "createdBy": "admin",
    "version": 1,
    "attributes": {
      "owner": "DataEngineeringTeam",
      "data_tier": "Raw",
      "qualifiedName": "raw_zone.customers_raw@lab_cluster",
      "name": "customers_raw",
      "description": "Raw ingestion table for customer records"
    },
    "relationshipAttributes": { ... }
  }
}
```

**Read by unique attribute** (no GUID needed — URL-encode the value, note `%40` for `@`):

```bash
curl -s -u admin:admin \
  "$A/api/atlas/v2/entity/uniqueAttribute/type/my_custom_table?attr:qualifiedName=raw_zone.orders_raw%40lab_cluster"
```

Returns the same shape as by-GUID, or **404** if not found — this is the idempotency trick `smoke_test.sh` uses.

**Update** — same `POST /entity` endpoint, but include the `guid`:

```bash
curl -s -u admin:admin -X POST -H "Content-Type: application/json" \
  -d @- "$A/api/atlas/v2/entity" <<'EOF'
{
  "entity": {
    "guid": "822d3513-f2ed-48c4-a6d7-047cbcad94ed",
    "typeName": "my_custom_table",
    "attributes": {
      "name": "customers_raw",
      "qualifiedName": "raw_zone.customers_raw@lab_cluster",
      "owner": "AnalyticsArchitectureTeam"
    }
  }
}
```

The response now reports `"mutatedEntities": {"UPDATE": [...]}`. Attributes you omit keep their values; attributes you set to `null` are cleared. (In 2.1.0 the dedicated partial-update endpoint `PUT /entity/guid/{guid}` is broken — see §5 — so POST-with-guid is *the* update mechanism.)

**Delete (soft):**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -u admin:admin -X DELETE \
  "$A/api/atlas/v2/entity/guid/<guid>"
# -> 200
curl -s -u admin:admin "$A/api/atlas/v2/entity/guid/<guid>" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['entity']['status'])"
# -> DELETED
```

**Bulk fetch** — several entities in one call:

```bash
curl -s -u admin:admin "$A/api/atlas/v2/entity/bulk?guid=<guid1>&guid=<guid2>"
# -> {"referredEntities": {...}, "entities": [...]}
```

### 4.4 Classification API

**Attach classifications** — the body is a **JSON array** (a single object yields HTTP 500 in 2.1.0):

```bash
curl -s -o /dev/null -w '%{http_code}\n' -u admin:admin -X POST \
  -H "Content-Type: application/json" \
  -d '[{"typeName":"PII","attributes":{"sensitivity":"high"}}]' \
  "$A/api/atlas/v2/entity/guid/822d3513-f2ed-48c4-a6d7-047cbcad94ed/classifications"
# -> 204 (no content = success)
```

**List an entity's classifications:**

```bash
curl -s -u admin:admin \
  "$A/api/atlas/v2/entity/guid/822d3513-f2ed-48c4-a6d7-047cbcad94ed/classifications"
```

```json
{
  "list": [
    {
      "typeName": "PII",
      "attributes": { "sensitivity": "high" },
      "entityGuid": "822d3513-f2ed-48c4-a6d7-047cbcad94ed",
      "entityStatus": "ACTIVE",
      "propagate": true,
      "removePropagationsOnEntityDelete": false
    }
  ],
  "totalCount": 1
}
```

Note `propagate: true` — the tag flows to lineage descendants.

**Remove a classification** — note the **singular** `classification` in the path (the plural path is not registered in 2.1.0 and returns 500):

```bash
curl -s -o /dev/null -w '%{http_code}\n' -u admin:admin -X DELETE \
  "$A/api/atlas/v2/entity/guid/<guid>/classification/PII"
# -> 204
```

**Search by classification** — see §4.5.

### 4.5 Search API

**Basic search** — filter by type, classification, and/or a `query` string; Solr-backed:

```bash
# all entities of a type
curl -s -u admin:admin "$A/api/atlas/v2/search/basic?typeName=my_custom_table&excludeDeletedEntities=true&limit=10"

# entities of a type tagged PII
curl -s -u admin:admin "$A/api/atlas/v2/search/basic?classification=PII&limit=10"
```

Response shape (trimmed):

```json
{
  "queryType": "BASIC",
  "searchParameters": { "classification": "PII", "excludeDeletedEntities": false, "limit": 10, ... },
  "approximateCount": 1,
  "entities": [
    {
      "guid": "822d3513-f2ed-48c4-a6d7-047cbcad94ed",
      "typeName": "my_custom_table",
      "status": "ACTIVE",
      "attributes": { "qualifiedName": "raw_zone.customers_raw@lab_cluster", "name": "customers_raw" }
    }
  ]
}
```

The `query` param narrows further (matched against name/qualifiedName-ish fields):

```bash
curl -s -u admin:admin "$A/api/atlas/v2/search/basic?query=orders_raw&typeName=my_custom_table&limit=10"
```

**DSL search** — Atlas's type-oriented query language:

```bash
# a type and its subtypes
curl -s -u admin:admin "$A/api/atlas/v2/search/dsl?query=from%20DataSet&limit=10"

# with a where-clause
curl -s -u admin:admin \
  "$A/api/atlas/v2/search/dsl?query=from%20my_custom_table%20where%20data_tier%3D%22Raw%22&limit=10"
```

DSL supports `where` on attributes (verified working), plus `isa`/`has` operators — which in 2.1.0 return empty results (see §3.7), so use basic search's `classification` param for tag filtering. Quirk: DSL responses report `"approximateCount": -1` — don't rely on it.

**Full-text search** — `GET /api/atlas/v2/search/fulltext?query=orders`. In this lab it returns empty results: the `fulltext_index` Solr collection exists but is never populated in Atlas 2.1.0's embedded setup. Use `basic`/`dsl` instead.

### 4.6 Lineage API

Lineage is created by making a **`Process`** entity whose `inputs`/`outputs` reference datasets:

```bash
curl -s -u admin:admin -X POST -H "Content-Type: application/json" \
  -d @- "$A/api/atlas/v2/entity" <<'EOF'
{
  "entity": {
    "typeName": "Process",
    "attributes": {
      "name": "etl_clean_orders",
      "qualifiedName": "pipeline.etl_clean_orders@lab_cluster",
      "description": "Cleans and conforms raw orders into the curated zone",
      "inputs":  [{"guid": "<orders_raw-guid>", "typeName": "my_custom_table"}],
      "outputs": [{"guid": "<orders_curated-guid>", "typeName": "my_custom_table"}]
    }
  }
}
EOF
```

Then query the lineage graph — **`direction` must be uppercase** (`BOTH`/`INPUT`/`OUTPUT`; lowercase → HTTP 500 in 2.1.0):

```bash
curl -s -u admin:admin \
  "$A/api/atlas/v2/lineage/<orders_raw-guid>?direction=BOTH&depth=3"
```

```json
{
  "baseEntityGuid": "eda75409-...",
  "lineageDirection": "BOTH",
  "lineageDepth": 3,
  "guidEntityMap": {
    "eda75409-...": { "typeName": "my_custom_table", "attributes": { "qualifiedName": "raw_zone.orders_raw@lab_cluster", ... } },
    "5145b23f-...": { "typeName": "Process",         "attributes": { "qualifiedName": "pipeline.etl_clean_orders@lab_cluster", ... } },
    "a66fc707-...": { "typeName": "my_custom_table", "attributes": { "qualifiedName": "curated_zone.orders_curated@lab_cluster", ... } }
  },
  "relations": [
    { "fromEntityId": "eda75409-...", "toEntityId": "5145b23f-...", "relationshipId": "6b0f1a03-..." },
    { "fromEntityId": "5145b23f-...", "toEntityId": "a66fc707-...", "relationshipId": "..." }
  ]
}
```

`relations` are the edges (dataset → process → dataset); `guidEntityMap` gives you the vertices. The Atlas web UI draws its lineage graph from exactly this. You'll also see the edges materialized on the entities themselves as `relationshipAttributes.inputToProcesses` / `outputFromProcesses`.

### 4.7 Glossary (via entity + relationship APIs)

Atlas 2.1.0 has **no `/v2/glossaries` REST API** (it was added in later Atlas versions; calling it here yields a 500 with `NotFoundException` in the log). Glossaries and terms are plain entities:

**Create a glossary:**

```bash
curl -s -u admin:admin -X POST -H "Content-Type: application/json" \
  -d @- "$A/api/atlas/v2/entity" <<'EOF'
{
  "entity": {
    "typeName": "AtlasGlossary",
    "attributes": {
      "name": "Retail Glossary",
      "qualifiedName": "retail_glossary@lab_cluster",
      "shortDescription": "Business terms for the retail data platform"
    }
  }
}
EOF
```

**Create a term anchored to the glossary** — `anchor` is a *relationship attribute* and works at create time:

```bash
curl -s -u admin:admin -X POST -H "Content-Type: application/json" \
  -d @- "$A/api/atlas/v2/entity" <<'EOF'
{
  "entity": {
    "typeName": "AtlasGlossaryTerm",
    "attributes": {
      "name": "Orders",
      "qualifiedName": "term.orders@lab_cluster",
      "shortDescription": "Transactional order records"
    },
    "relationshipAttributes": {
      "anchor": {"guid": "<glossary-guid>", "typeName": "AtlasGlossary"}
    }
  }
}
EOF
```

**Assign the term to an entity** — this is a relationship instance (`AtlasGlossarySemanticAssignment`), created via the **Relationship API**. Two 2.1.0 quirks: the POST body is the relationship object **unwrapped**, while GET responses wrap it in `{"relationship": ...}`:

```bash
curl -s -u admin:admin -X POST -H "Content-Type: application/json" \
  -d @- "$A/api/atlas/v2/relationship" <<'EOF'
{
  "typeName": "AtlasGlossarySemanticAssignment",
  "end1": {"guid": "<term-guid>", "typeName": "AtlasGlossaryTerm"},
  "end2": {"guid": "<entity-guid>", "typeName": "my_custom_table"}
}
EOF
```

Response (trimmed):

```json
{
  "typeName": "AtlasGlossarySemanticAssignment",
  "guid": "c5ec3c39-9736-468d-8c34-16ea11534430",
  "end1": { "guid": "e9890be4-...", "typeName": "AtlasGlossaryTerm", "uniqueAttributes": { "qualifiedName": "term.orders@lab_cluster" } },
  "end2": { "guid": "eda75409-...", "typeName": "my_custom_table",   "uniqueAttributes": { "qualifiedName": "raw_zone.orders_raw@lab_cluster" } },
  "status": "ACTIVE",
  "propagateTags": "ONE_TO_TWO"
}
```

Verify both sides: the term gains `relationshipAttributes.assignedEntities`, and the entity gains `relationshipAttributes.meanings` (this is what the UI shows as the entity's terms):

```bash
curl -s -u admin:admin "$A/api/atlas/v2/entity/guid/<entity-guid>" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['entity']['relationshipAttributes']['meanings'])"
```

### 4.8 Audit API

Every mutation is logged. The endpoint lives on the **legacy path** (no v2 equivalent in 2.1.0):

```bash
curl -s -u admin:admin "$A/api/atlas/entities/<guid>/audit" \
  | python3 -c "
import json, sys
for e in json.load(sys.stdin)['events']:
    import datetime
    print(datetime.datetime.fromtimestamp(e['timestamp']/1000).isoformat(), e['user'], e['action'])"
```

```
2026-11-05T00:04:07 admin ENTITY_UPDATE
2026-11-05T00:04:01 admin ENTITY_UPDATE
2026-11-05T00:03:51 admin ENTITY_UPDATE
2026-11-04T23:02:17 admin ENTITY_CREATE
```

Each event's `details` field carries the full before/after entity JSON.

---

## 5. Atlas 2.1.0 quirks & gotchas

All verified on this lab. These are the things that cost hours if you don't know them:

| # | Quirk | Workaround |
|---|---|---|
| 1 | No v2 admin endpoints — `GET /v2/admin/status` → 500 | Use v1 `GET /api/atlas/admin/status`, parse `"Status"` (capital S) |
| 2 | `PUT /v2/entity/guid/{guid}` (partial update) → 400 `ATLAS-400-00-023 "Attribute null not found"` | Update via `POST /v2/entity` with the entity's `guid` |
| 3 | Classification POST body must be a **JSON array**; a single object → 500 | Wrap it: `[{...}]` (success is **204**, not 200) |
| 4 | Classification DELETE path is **singular** — `.../classification/{name}`; the plural path → 500 (`NotFoundException` in logs) | Use the singular path (returns 204) |
| 5 | Lineage `direction` param is case-sensitive: `both` → 500 | Use `BOTH` / `INPUT` / `OUTPUT` |
| 6 | No glossary REST API (`/v2/glossaries` → 500, `NotFoundException` in logs) | Manage glossaries/terms as entities + the relationship API (§4.7) |
| 7 | No audit v2 endpoint | Use legacy `GET /api/atlas/entities/{guid}/audit` |
| 8 | `DELETE` is soft; `?purge=true` accepted but is a **no-op** | Deleted entities stay in graph/search with `status: DELETED` |
| 9 | Basic search **includes deleted entities** by default | Pass `excludeDeletedEntities=true` |
| 10 | Duplicate typedef registration → 409 `ATLAS-409-00-001`; type deletion blocked by any referenced entity (even deleted) → 409 `ATLAS-409-00-002` | Check-then-create (see `smoke_test.sh`); design types to last |
| 11 | No `qualifiedName` dedup — re-POST creates duplicates | Look up by uniqueAttribute first |
| 12 | Full-text search returns empty (fulltext Solr collection never populated in embedded mode) | Use basic/DSL search |
| 13 | DSL `isa` / `has` operators return empty results | Filter classifications via basic search's `classification` param |
| 14 | DSL search reports `approximateCount: -1` | Count client-side if needed |
| 15 | Search index (Solr) lags writes by seconds (async via Kafka) | Poll/retry search assertions; GET-by-guid is immediate |
| 16 | Relationship API asymmetry: POST takes an unwrapped body, GET returns `{"relationship": ...}` | Wrap/unwrap accordingly |

## 6. v1 vs v2 APIs

Atlas carries two REST generations:

* **v2** (`/api/atlas/v2/...`) — JSON-object oriented (`{"entity": {...}}`), the one to learn; everything in this guide uses it except where noted.
* **v1 / legacy** (`/api/atlas/entities`, `/api/atlas/types`, `/api/atlas/admin`) — older, still hosts some endpoints that never got a v2 port in this version: **admin status** and **audit**. Expect XML-ish/`requestId`-wrapped responses.

Rule of thumb: try v2 first; when an endpoint 500s with `NotFoundException` in `application.log`, it probably only exists on the legacy path.

## 7. The lab's worked example

The validation of this guide left a small end-to-end story in the lab — explore it in the Atlas UI (http://localhost:21000, admin/admin) or via the dashboard (http://localhost:8501):

| Object | qualifiedName | Notes |
|---|---|---|
| Raw table | `raw_zone.orders_raw@lab_cluster` | from Part 1; owner `AnalyticsArchitectureTeam`; term **Orders** attached |
| Curated table | `curated_zone.orders_curated@lab_cluster` | `data_tier: Curated` |
| ETL process | `pipeline.etl_clean_orders@lab_cluster` | inputs: orders_raw; outputs: orders_curated → open **Lineage** in the UI |
| Customer table | `raw_zone.customers_raw@lab_cluster` | tagged **PII** (`sensitivity: high`) |
| Glossary | `retail_glossary@lab_cluster` | type `AtlasGlossary` |
| Term | `term.orders@lab_cluster` | type `AtlasGlossaryTerm`, anchored to the glossary, semantically assigned to orders_raw |
| Deleted demo | `tmp.temp_demo@lab_cluster` | soft-deleted; still visible in basic search with `status: DELETED` (quirk #8) |

Lineage to try in the UI: search `orders_raw` → open it → the **Lineage** tab shows `orders_raw → etl_clean_orders → orders_curated`.

## 8. Suggested exercises

1. **Types:** extend `my_custom_table` is not possible (types are immutable-ish) — instead register `my_secure_table` extending `DataSet` with a `pii_columns` attribute.
2. **Entities:** create `my_secure_table` entity `raw_zone.payments_raw@lab_cluster`; verify with uniqueAttribute lookup.
3. **Classifications:** create a `SOX` classificationDef; tag `orders_curated` with it; check propagation by looking at `orders_curated`'s `propagatedClassifications` after tagging `orders_raw` with `PII`.
4. **Lineage:** add a second process `pipeline.etl_orders_to_reports@lab_cluster` consuming `orders_curated` into a new `reporting.orders_daily@lab_cluster`; view the 3-hop lineage of `orders_raw`.
5. **Glossary:** add a term `Customer` and assign it to `customers_raw` via the relationship API.
6. **Search:** answer "which Raw-tier datasets are tagged PII?" — try DSL `from DataSet isa PII where data_tier = "Raw"` first, discover it returns empty (quirk #13), then solve it with basic search: `?typeName=DataSet&classification=PII&query=` plus a client-side filter on `data_tier`.
7. **Audit:** fetch the audit of `orders_raw` and identify every owner change this lab's history (Part 1 smoke test, dashboard test, this guide).

---

*Validated against: Apache Atlas 2.1.0 (`sburn/apache-atlas:2.1.0`), Ubuntu 24.04 host, on 2026-11-05. Official docs: https://atlas.apache.org/2.1.0/index.html*
