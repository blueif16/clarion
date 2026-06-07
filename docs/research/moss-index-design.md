# Research — Moss index design: per-site indexes vs. one category index + metadata filter

_Researched 2026-06-06. Question owner: the per-site `clarion-site-<host>` design shipped
this session. Sources: the Moss docs (`docs.moss.dev`), the installed `moss` SDK (1.2.0),
the Moss changelog/pricing, and vector-DB multi-tenancy best practice (Qdrant, MongoDB,
Pinecone, Milvus, AWS Bedrock, Weaviate) via Exa. Citations at the bottom._

---

## The question

We shipped a crawler that writes **one Moss index per website** (`clarion-site-www-usa-gov`,
`clarion-site-<host>`, …). Should we instead keep **one index per CATEGORY of data**
(e.g. one "site structure" index for all sites) and separate sites with **metadata**?

## Status

**IMPLEMENTED 2026-06-06.** The migration below is done: the crawler writes all sites
into one `clarion-site-structure` index tagged with `{site}`; `MossClient.search` /
`MossRetriever.query` expose `filter`; `SiteKnowledge` scopes by `site $eq <host>`. The
per-site `clarion-site-www-usa-gov` index was backfilled and deleted. Verified live:
filtered consult returns the site's pages; a non-matching site filter returns 0 (the
filter genuinely isolates). Active project now holds just `clarion-kb` +
`clarion-site-structure`.

## TL;DR — recommendation

**Yes. Switch to category indexes + metadata filtering.** One index per *kind* of
knowledge (site-structure, task-paths, policy-KB, user-profile), every chunk tagged with
`{site, url, …}` metadata, and queries scoped with `QueryOptions.filter={"field":"site",
"condition":{"$eq": host}}`. This is:

- **Supported** — Moss 1.2.0 has query-time metadata filtering (confirmed in the installed
  SDK signature *and* the docs); we just don't expose `filter` in our wrapper yet.
- **The industry-default** — "a single collection per embedding model with payload-based
  partitioning" (Qdrant); "one collection, distinguish by `tenant_id` pre-filter"
  (MongoDB). Many-indexes is reserved for *hard isolation* or *few tenants*.
- **The right fit for us** — our "sites" are **not** security tenants, they share **one
  embedding model** (Gemini custom), and one-index-per-site **explodes the index count**
  (we already hit the Moss free-tier 3-index cap). None of the reasons to prefer
  many-indexes apply; all the reasons to prefer one-index-+-filter do.

It does **not** weaken the kernel invariant: structure facts are cross-page planning
context, never spoken page values (those stay live on `PageRetriever`).

---

## Decisive finding — Moss DOES support metadata filtering

Confirmed two independent ways.

**1. The installed SDK (`moss==1.2.0`), introspected:**
```
QueryOptions(embedding=None, top_k=None, alpha=None, filter=None)
DocumentInfo(id, text, metadata=None, embedding=None)
MossClient.query(name, query, options: QueryOptions) -> SearchResult
MossClient.query_multi_index(...)   # multi-index search exists
```
→ `filter` is a real `QueryOptions` field. Our `clarion/retrieval/moss_client.py:search`
builds `QueryOptions(top_k, alpha, embedding)` and **omits `filter`** — that's the only gap.

**2. The docs (`/docs/reference/python/metadata-filtering`, changelog):**
- Operators: `$eq`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte`, `$in`, `$nin`, `$near`
  (geo-haversine), composed with `$and` / `$or` (arbitrarily nestable). A single condition
  needs no wrapper.
- Filter shape:
  ```python
  QueryOptions(top_k=5, filter={"field": "site", "condition": {"$eq": "www.usa.gov"}})
  # composite:
  QueryOptions(top_k=5, filter={"$and": [
      {"field": "site",     "condition": {"$eq": "www.usa.gov"}},
      {"field": "category", "condition": {"$eq": "structure"}},
  ]})
  ```
- **Critical caveat:** filtering is evaluated **only on a locally `load_index()`-ed index**
  — "When `filter` is passed but the index is not loaded locally, a warning is logged and
  the filter is skipped (cloud query API does not yet support filtering)." Our
  `MossRetriever._ensure_loaded()` already `load_index`es before querying (the sub-10ms
  local path), so **filtering works for us by construction**.
- Metadata values are stringly-typed (numbers auto-stringified for matching). Our
  `MossDoc.metadata: dict[str,str]` already matches.

**Also available (relevant to design):**
- `query_multi_index(names, query, options)` — search several loaded indexes, global top-K,
  each hit tagged with `index_name`. **Embedding-only; `alpha` ignored.** (Useful if we ever
  want cross-category retrieval in one call.)
- Per-index embedding model choice (`moss-minilm` fast / `moss-mediumlm` accurate / custom).
- `load_indexes([...])` / `unload_indexes([...])` bulk lifecycle; `auto_refresh` on load.

## The index cap is a pricing tier, not a hard wall

From `/docs/pricing`:

| Plan | Indexes | Storage | Projects |
|---|---|---|---|
| **Developer (free, $5 credits)** | **3** | 500 MB | 1 |
| Hobbyist ($30/mo) | **Unlimited** | 2 GB | Unlimited |
| Startup ($200/mo) | Unlimited | 10 GB | Unlimited |
| Enterprise | Unlimited | Custom | Unlimited |

So the `429 USAGE_LIMIT_EXCEEDED: Index limit of 3` we hit was the **free Developer tier**.
Upgrading lifts it — **but "unlimited indexes" is not a reason to keep per-site indexes.**
Even with no cap, per-category + filter is the better design (below), and it keeps us inside
the free tier (4 category indexes ≈ 1 plan bump at most, vs. unbounded growth: 1 per site).

---

## Industry best practice — the one-vs-many decision

Unanimous across vector DBs: **default to ONE index/collection partitioned by metadata; use
separate indexes only for hard isolation or a small, fixed tenant set.**

| Source | Default guidance |
|---|---|
| **Qdrant** | "In most cases, a single collection per embedding model with payload-based partitioning for different tenants and use cases." Multiple collections only "when you have a limited number of users and you need isolation." Warns: hundreds–thousands of collections → "resource overhead… performance degradation and cluster instability" (cloud cap 1000). |
| **MongoDB Vector Search** | "We recommend storing all tenant data in a single collection… distinguish tenants by a `tenant_id` field… used as a pre-filter." Explicitly **does not recommend** collection-per-tenant (no isolation benefit, operational/perf cost). |
| **Pinecone** | Namespace-per-tenant for *isolation/cost at scale*; **metadata filtering** "when tenant isolation is not a strict requirement, or when you need to query across tenants." (Moss has no namespaces; its analogue is metadata filter.) |
| **Milvus** | Partition-key / single-collection scales to ~10M tenants; collection-per-tenant hits a collection cap (~10k) and resource overhead. |
| **AWS Bedrock KB** | Single knowledge base + metadata filtering for multi-segment data → cost optimization + segmentation. |

**When many-indexes wins (none apply to Clarion site-structure):**
- *Hard security isolation* between tenants ("no filter to forget"). — Our sites are all the
  **same user's** structural knowledge; not a security boundary.
- *Per-tenant embedding model / chunking.* — We use **one** model (Gemini custom) and one
  chunking strategy for all sites.
- *Few, fixed tenants.* — Sites are **open-ended and growing**; per-site = unbounded indexes.

**When one-index-+-filter wins (all apply):** open-ended tenant count, shared model, trivial
onboarding (just tag metadata), lower cost, stays under index caps. The only real costs —
(a) filter must be correct/trusted, (b) filtered query still scans the loaded index — are
negligible here: the `site` value comes from the **live page URL** (trusted, not user
input), and a Clarion structure index is tiny and fully in-memory (Moss sub-10ms).

---

## Recommended design for Clarion's knowledge layer

Four **category** indexes, each metadata-partitioned, instead of N×sites + others:

| Index | Holds | Key metadata | Typical query filter |
|---|---|---|---|
| `clarion-site-structure` | page affordances/headings (the crawler's output) for ALL sites | `{site, url}` | `site $eq <host>` |
| `clarion-task-paths` | subgoal plans we've run (knowledge-layer 4b) | `{site, goal_kind}` | `site $eq <host>` (+ `goal_kind`) |
| `clarion-kb` | policy/document KB (existing) | `{doc, source}` | (optional) |
| `clarion-profile` | user traits/prefs (knowledge-layer 4c) | `{user_id}` | `user_id $eq <uid>` |

Chunk metadata for the structure index becomes, per page:
`metadata = {"site": host, "url": url, "category": "structure"}`.

Query (the planner's consult) becomes:
```python
QueryOptions(top_k=k, embedding=vec,
             filter={"field": "site", "condition": {"$eq": host}})
```

This collapses unbounded `clarion-site-<host>` indexes → **one** structure index, scales to
any number of sites within the free tier, and keeps cross-site queries possible (omit the
filter, or `query_multi_index` across categories).

---

## Migration plan (per-site → category + filter)

Small, mechanical, behind the existing `CLARION_SITE_KNOWLEDGE` flag. ~4 edits:

1. **`MossClient.search`** (`retrieval/moss_client.py`): add `filter: Optional[dict] = None`
   param → `QueryOptions(..., filter=filter)`. (The SDK already accepts it.)
2. **`MossRetriever.query`** (`retrieval/retriever_moss.py`): add an optional `filter`
   passthrough to `search`. (Frozen `Retriever.query` signature stays; add `**`-style or a
   sibling method `query_filtered` to avoid touching the ABC.)
3. **`site_indexer`**: write all pages into the single `clarion-site-structure` index with
   `metadata={"site": host, "url": url, "category":"structure"}`; drop `index_name_for`'s
   per-host index (keep host derivation for the metadata value).
4. **`SiteKnowledge.context_facts`**: query `clarion-site-structure` with
   `filter={"field":"site","condition":{"$eq": host}}` instead of selecting a per-host index.

Net: delete the per-site `clarion-site-www-usa-gov` index after backfilling structure into
`clarion-site-structure`. Tests stay green (the consult is still gated + fail-open).

## Caveats / risks

- **Filter only applies to a locally loaded index** — fine for us (`MossRetriever` always
  `load_index`es), but any future *cloud* query path would silently drop the filter (Moss
  logs a warning). Keep retrieval on the local path.
- **Trust the filter value** — `site` must come from the live URL/host, never user text
  (filter-injection is a real RAG risk; not exposed here since we derive host ourselves).
- **One big in-memory index** — all sites' structure load together on first query. At our
  scale (dozens of small structure docs) this is trivial; revisit only at very large scale.
- **`query_multi_index` ignores `alpha`** (embedding-only) — if we ever do cross-category
  retrieval in one call, hybrid keyword weighting won't apply there.

## Sources

- Moss docs: `/docs/start/core-concepts`, `/docs/reference/python/metadata-filtering`,
  `/docs/integrate/retrieval`, `/docs/changelog`, `/docs/pricing` (docs.moss.dev, fetched
  2026-06-06); installed `moss==1.2.0` SDK introspection; `github.com/usemoss/moss` README;
  `pypi.org/project/moss`.
- Multi-tenancy best practice (Exa): Qdrant Multitenancy docs; MongoDB Vector Search
  multi-tenant architecture; Pinecone "Implement multitenancy" / "Design for multi-tenancy";
  Zilliz/Milvus multi-tenancy best practices; AWS "Multi-tenancy in RAG … single Bedrock KB
  with metadata filtering"; "Architecting Multi-Tenant RAG: one vs many vector DB" (Data
  Engineer Things); "Vector Store Access Control" (tianpan.co).
