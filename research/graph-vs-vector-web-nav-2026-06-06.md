# Graph vs flat vector for agent web-navigation, hybrid patterns, Moss integration & crawl surface — research brief
_scope: 2024–2026, generic (LLM-agent / RAG) lens, deep dive • generated 2026-06-06_
_source tags: [R]=Reddit • [Y]=YouTube (yt-rag) • [E]=Exa web. Inline citations name the specific creator/site so every claim is traceable._
_Context: Clarion indexes page STRUCTURE (headings + affordances, no live values) as flat chunks in Moss, partitioned by `{site}` metadata, retrieved by similarity filtered to host. Question: add a GRAPH? How to combine with Moss? Which crawl surface?_

## How to read this
Every claim is tagged by source leg and named. Where a claim is practitioner-experience (Reddit/YouTube) vs benchmarked (papers/blogs via Exa), it's marked. The single most on-point source for Clarion's exact architecture is **WebNavigator** [E] — read that row first.

## TL;DR
- **A graph is worth it for Clarion's job, but only the *navigation/path* half — not as a replacement for the vector index.** The consensus across all three legs: vectors answer "which page *looks like* my goal"; a graph answers "what's the *path of pages/actions* to get there." They are complementary, not either/or. [E][Y][R]
- **The flat vector space has no edges — that is its defining limitation for multi-hop/path queries**, and it's exactly the limitation a site-transition graph removes. [R taxonomy post, 127 upvotes][E vishnukdev]
- **The reference architecture already exists and matches Clarion almost exactly: WebNavigator** [E] — offline *zero-token, no-LLM* crawl from a single homepage URL builds a directed interaction graph (DOM/AXTree/screenshot per node), **all nodes are embedded into a vector DB**, then online **Retrieve (vector) → Reason (selector) → Teleport (shortest-path on the graph)**. This is "flat index + graph," productionized.
- **Moss cannot host the graph.** Moss 1.2.0 is a vector+keyword *search runtime*, not a database — verified by SDK introspection (no graph/edge/traverse symbols) and the Moss docs/roadmap. Its "integrations" are all *framework retriever* adapters (LangChain, LiveKit, Pipecat, DSPy, LlamaIndex…) + an MCP server; **none are graph integrations, and there is no graph on the roadmap.** [E moss docs/github] So the graph must live *beside* Moss, not inside it.
- **The worth-it threshold is real and measurable:** graph helps multi-hop (+31%) but *hurts* single-hop lookups (−9%) and adds latency (9s→22s) [E data-and-beyond]. So gate the graph to path/navigation queries; keep pure vector for "what's on this page."
- **Crawl surface:** never `connectOverCDP` to the user's primary browser (root over web identity, MFA-bypass) [E debugg.ai]. Clarion's **extension relay is already the recommended "delegated real-browser access" pattern** [E dev.to/eliofbm] — commands flow through, cookies stay in the user's browser. The clean Playwright side-browser is safe but *can't see authenticated/next pages* — which is the user's exact objection.

---

## Key findings (in depth)

### A. Graph vs flat vector — the verdict for navigation
The contested-but-clear practitioner consensus: **a knowledge graph is a targeted tool, not a default, and it earns its place precisely when the query is about *relationships/paths* rather than *similarity*.**

- The defining distinction, stated bluntly: a flat vector space **"has zero concept of relationships because the data structure has no edges."** Vector RAG works for "FAQ bots, documentation search, simple Q&A… documents are self-contained" and breaks "the moment your answer requires connecting facts across documents." [R, r/Rag taxonomy post, 127 upvotes] Graph RAG is *for* "multi-hop reasoning like 'How is Person A connected to Event C through Company B?'" [same]
- A clean trigger heuristic: **"If your question contains 'through', 'via', 'related to', 'impacted by', 'depending on' — it's a traversal question, not a similarity question."** [E, dev.to/vishnukdev] Clarion's navigation question — *"what sequence of pages/clicks gets from here to the goal"* — is structurally a traversal question.
- But the louder practitioner camp warns graph is **overhyped and operationally painful**: LLM entity-extraction is noisy, entity-resolution is "a headache," graphs *drift* and re-indexing is costly, and **"nobody confirms a gain over well-tuned hybrid past a few thousand docs."** Several teams retreated to BM25+dense+RRF hybrid, to SQL, or even to flat Markdown. [R r/Rag scaling-skeptic; R r/LocalLLaMA "went back to SQL", 267 upvotes; R r/LLMDevs KG-vs-Markdown]
- **Crucial nuance that resolves the contradiction:** the anti-graph pain is almost entirely about *building a semantic knowledge graph by extracting entities/relations from prose with an LLM* (expensive, noisy, drifts). **Clarion's graph is nothing like that** — its edges are *literal hyperlinks / affordance-transitions* harvested deterministically from the DOM at crawl time. No LLM extraction, no entity resolution, no ontology design. Most of the cited cost simply doesn't apply.

### B. What the graph actually buys for navigation (the "URL pipeline" intuition — confirmed)
The user's framing — *"a graph could give us the pipeline of URLs of the workflow"* — is exactly what the navigation literature does, and it's the half vectors cannot do:

- **WebNavigator** [E, arxiv 2603.20366] is the keystone. It "reframes navigation from a probabilistic reasoning challenge into a deterministic retrieval and planning problem." Offline: a heuristic engine crawls the site into a directed **Interaction Graph** (DOM tree + AXTree + screenshot per node) **"with zero-token cost, requiring no LLM involvement and only a homepage URL as input,"** then **"all nodes are embedded and indexed into a vector database."** Online **Retrieve-Reason-Teleport**: vector-retrieve top-k candidate observations → a multimodal selector picks the best target → **"a pathfinding algorithm computes the shortest trajectory to teleport the agent to the target observation at zero-token cost."** This *is* the flat-index + graph hybrid, and the graph's whole job is producing the URL/action path.
- Classical pathfinding over a page graph is a recurring idea: **"if you have a way to map this grid map into nodes and edges… do A\* search… BFS, DFS, Dijkstra, Floyd-Warshall… to do navigation,"** with a *hierarchical* planner (inter-region transitions first, then within-region) — and keep "memory symbolic, knowledge graphs… tool use symbolic," focusing the LLM "purely on pattern matching." [Y, John Tan Chong Min, https://youtu.be/1Yaf6OSCRkk?t=3568, ?t=4569]
- **Why the LLM alone fails the path** (the motivating failure): a one-screen-at-a-time navigate tool loops because **"if the destination is 10 screens away, by the fifth screen it forgot where it wants to go."** [Y, same, ?t=553] A precomputed path doesn't forget. Directly relevant to long-horizon gov-site flows.
- Adjacent prior art confirming "site as state-transition graph": **LASER** (web nav as state-space exploration, per-state action sets enable backtracking/error recovery) [E arxiv 2309.08172]; **R2D2** (replay-buffer builds a dynamic *map* of visited pages → "50% fewer navigation errors, 3× task completion" on WebArena) [E aclanthology 2025.acl-long.1464]; **WebDreamer / WMA** (LLM as *world model* that "dreams" the next state before acting — explicitly to avoid irreversible web actions backtracking can't undo) [E arxiv 2411.06559, 2410.13232].

> ⚠️ The WMA/WebDreamer "don't take irreversible actions you can't simulate-then-undo" framing is a direct echo of Clarion's agentic invariant — worth citing when we justify the design.

### C. The most efficient hybrid + the worth-it threshold
The documented production-standard is **vector-first, graph-expansion** — and it's cheap when the graph is small:

- Pattern: embed query → ANN retrieve entry nodes (vector) → traverse typed edges 2–3 hops → fuse. **"Default to 2 hops… 3 hops only when the query explicitly requires multi-step reasoning… hard cap at 6 hops… limit to 100 neighbors per node… target 150–350 input tokens of graph context."** `hybrid_score = α × vector_score + (1−α) × graph_score`. Hybrid yields "15–30% improvements in faithfulness and answer relevancy." [E, medium/graph-praxis hybrid-patterns]
- **The threshold (decision rule):** add the graph **only when failures trace to inability to follow relationship chains, not inability to find similar content.** [E atlan.com] Benchmarked cost of getting this wrong: GraphRAG **+31.3% on multi-hop (0.700 vs 0.534) but −9.1% on single-hop (0.640 vs 0.704), and 9s → 22s** because "entity extraction ran first, found nothing useful for a simple lookup, and added noise." [E data-and-beyond] → **Gate the graph to navigation/path queries; never route a "what's on this page" query through it.**
- **For 2–3 hop traversals you do NOT need a graph database.** "Postgres or MongoDB handles documents, vectors, and graph lookups in a single system… You only really need Neo4j when deep traversals or specialized graph algorithms are core. Don't design for Google scale when you're processing thousands of documents." [R, r/AI_Agents agentic-GraphRAG] Single-system hybrids exist (MongoDB `$graphLookup` + Atlas Vector; FalkorDB single-Cypher `db.idx.vector.queryNodes → MATCH …-[*1..2]-…`) [E falkordb]. **But Clarion's structure set is tiny and the adjacency is just hyperlinks — a plain in-process adjacency map (dict / networkx), persisted as its own artifact next to the Moss index, is lighter than any of these and keeps the local-first, no-infra property.**
- Reassurance on cost trend if we ever did do LLM extraction: the GraphRAG indexing premium collapsed **"$33,000 → $33 (Microsoft, ~18 months)."** [E graph-praxis cost-cliff] And a dissent worth heeding: **UnWeaver — "VectorRAG is almost enough"** — entity decomposition captures most GraphRAG gain without the graph-index complexity [E arxiv 2603.29875]. Translation: don't over-build; the minimal adjacency is likely enough.

### D. Moss + graph integration — the user's specific question, answered
**Moss has no graph capability, no graph plugin, and no official "combine with a graph" workflow. The graph must live outside Moss.**

- **SDK introspection (authoritative):** `moss==1.2.0` top-level exports and all `MossClient` methods are vector/index/doc ops — `add_docs, create_index, query, query_multi_index, load_index, get_docs, delete_*` + `QueryOptions(filter=…)`. **Zero** symbols matching graph/edge/relation/traverse/neighbor/node-adjacency. [verified locally, `.venv/bin/python -c "import moss…"`]
- **Moss's own positioning:** "Moss isn't a database! It's a **search runtime**." Features = sub-10ms hybrid (semantic + keyword) search, built-in embeddings (`moss-minilm`/`moss-mediumlm`) or BYOE, metadata filtering (`$eq/$and/$in/$near`), WASM browser build. [E github.com/usemoss/moss, docs.moss.dev]
- **What "plugins / official workflows" Moss actually has** — and why none solve the graph need: framework *retriever* integrations (LangChain, DSPy, LlamaIndex, CrewAI, AutoGen, Haystack, Mastra, Pydantic AI, **Pipecat, LiveKit, Vapi, ElevenLabs**, Strands, Next.js, Vercel AI SDK), an **MCP server** (`@moss-tools/mcp-server`), and **data connectors** that *ingest from* SQLite/MongoDB/MySQL/Supabase. [E github/docs] These drop Moss in as the *vector retrieval node*; they do not add edges or traversal. The roadmap's only graph-adjacent item is a **"LangGraph retrieval node"** — that's LangGraph (the agent-orchestration framework Clarion's kernel already uses), **not** a knowledge graph. [E ROADMAP.md]
- **One Moss feature that *is* directly useful** to wire a graph alongside: the **"Context Injection" pattern — "Moss is queried automatically on every user message and results are injected into the LLM context… faster than tool calling because there's no LLM thinking step to decide whether to search."** [E moss.dev LiveKit guide] This is the auto-retrieval-per-turn the user described. The graph step (path-find) can hang off the *same* trigger: on the user turn, vector-retrieve the target node from Moss **and** compute the path from the current node over the adjacency map, inject both.

**Recommended shape for Clarion (maps WebNavigator onto our stack):**
| Concern | Where it lives |
|---|---|
| "Which page/affordance matches the goal" (semantic) | **Moss** `clarion-site-structure`, filtered `site $eq host` (today's design — unchanged) |
| "What's the path of pages/clicks to get there" (adjacency) | **New, separate tiny artifact**: a per-site directed graph `{node_id → (url, [out-edges: link/affordance → target node_id])}` built at crawl time from `collect_links` + affordances. Plain dict / `networkx`, persisted beside the Moss index, loaded in-process. No graph DB. |
| Node identity that ties them together | The **same `node_id`** keys both the Moss chunk (`metadata.node_id`) and the graph node, so a Moss hit → graph node is O(1). |
| Path-find | A\*/BFS over the adjacency at plan time → the "URL pipeline" → feed as the SITE MAP / plan hint (still structure, never spoken values — invariant intact). |

This keeps Moss doing exactly what it's good at (sub-10ms local vector+keyword), adds the graph as a feather-weight sidecar, and requires **no new infrastructure** and **no change to the kernel invariant** (the graph is cross-page *structure*, never live values).

### E. Crawl surface — dedicated browser vs the user's real tab
The user's instinct was right on both sides; the literature gives a clean middle path.

- **Hard rule:** **"never `connectOverCDP` to a user's running Chrome"** — handing an agent CDP on a real session is "root-equivalent control over your web identity… read HttpOnly cookies, harvest OAuth tokens, bypass MFA by replaying session state." [E debugg.ai]
- **Side Playwright browser (today's crawler):** safe and isolated, **but "removes the very state that makes work tasks valuable"** — it has no auth, so it literally **can't see the authenticated/next pages** (the user's "you can't even know what's the next page"). [E dev.to/eliofbm] Good for public structure (usa.gov), useless behind login.
- **Real authenticated tab:** sees everything but every navigation is consequential, and reading attacker-controlled page content is a prompt-injection blast radius — first-hand: **"invisible text on a help-center page… the agent followed the instructions and quietly started collecting data. Took 11 days to notice."** [R r/AI_Agents]
- **The middle ground Clarion is already positioned for — "Delegated real-browser access via a relay + extension"**: "Commands move through the system; the browser session remains the authority source." [E dev.to/eliofbm] **This is exactly the extension `chrome.debugger` relay** (`CLARION_ACTUATOR=extension`). The graph can be built **observationally, read-only, on the user's real tab without navigating** — harvest `<a href>` + affordances from the *current* page only, add nodes/edges lazily as the user (or a consented step) actually visits pages. No blind "should I click next?" risk.
- **Supporting patterns worth stealing:**
  - **"Browser for login, HTTP for data"** — authenticate once in the browser, hand cookies to a raw HTTP client for cheap read-only structure fetches. [E dev.to/nirberko, ravoid.com] Lets a side-process see authenticated *structure* without driving the user's tab — but cookie handoff must be scoped/expired/destroyed.
  - **Read-only href-harvest before rendering** — "pages are plaintext until proven otherwise; count `<a>` links; only delegate to headless when JS is actually needed." [E freeman.vc] An order-of-magnitude cheaper way to grow the graph.
  - **Session boundaries must wipe everything** — "cookies (incl. HTTP-only), localStorage, sessionStorage, IndexedDB, service-worker caches, HTTP cache" all survive `page.close()`; pooled profiles leak across tenants invisibly. [E tianpan.co]

**Recommendation:** build the graph from the **extension relay, read-only, lazily** (current page's links/affordances only, no speculative navigation) as the default product path; keep the **side Playwright crawler for public, unauthenticated sites** where a deeper offline BFS is safe and useful. Never attach CDP to the user's primary profile; if a side-process needs authenticated structure, use scoped cookie-handoff + raw HTTP GETs, not driven navigation.

---

## What's working (claimed)
- Vector-first → graph-expansion (2–3 hops, RRF fusion) is the converged production hybrid. [E graph-praxis, atlan] (benchmarked)
- AXTree/DOM "set-of-marks" structured perception + perceive→act→re-perceive loop is the dominant, working web-agent paradigm; DOM agents finished tasks in **68s vs 225s** for vision-only. [Y PY, https://youtu.be/WshRCrMbn8M?t=95] (practitioner/explainer)
- Offline interaction-graph + vector index + teleport (WebNavigator) makes navigation deterministic and zero-token on the hot path. [E] (paper, date unverified)
- Delegated relay+extension is the recommended way to get real-session access without root-over-identity risk. [E dev.to/eliofbm] (practitioner)

## What's broken / contested
- **GraphRAG-at-scale skepticism is strong** — extraction noise, entity-resolution, graph drift, re-index cost; "nobody scaled it past a few thousand docs" with a clean win over hybrid. [R r/Rag, r/LocalLLaMA] *Mitigant for us: our edges are deterministic hyperlinks, not LLM-extracted entities — most of this pain doesn't apply.*
- **Graph hurts single-hop** (−9%, +13s). Must be gated to path queries. [E data-and-beyond]
- **"Agentic RAG is mostly hype"** / clients use the agent layer to paper over bad data. [R r/AI_Agents, 211 upvotes] — keep the graph minimal; fix structure-extraction quality first.
- **Browser-imitation crawling is "architectural debt"** — fragile, bot-detected, breaks on site updates. [R r/AI_Agents] — argues for read-only/HTTP harvest over heavy headless driving where possible.
- **Authenticated reach == attack surface** (indirect prompt injection, session bleed). [R r/AI_Agents; E tianpan.co]

## Numbers worth verifying
- GraphRAG vs VectorRAG: **+31.3% multi-hop (0.700 vs 0.534); −9.1% single-hop (0.640 vs 0.704); 9s vs 22s.** [E data-and-beyond]
- Hybrid hop knobs: **2 hops default, ≤6 cap, ≤100 neighbors/node, 150–350 tokens graph context; 15–30% faithfulness lift.** [E graph-praxis]
- Multi-hop benchmark: pure vector **~60%** vs intent/utility-ranked **91%+** on STaRK. [R r/LocalLLaMA]; MemRL value-selection **+56%** on ALFWorld. [Y Emergent Mind]
- DOM vs vision web-agent: **68s vs 225s.** [Y PY]
- R2D2 page-map: **50% fewer nav errors, 3× completion (WebArena).** [E aclanthology]
- GraphRAG indexing cost cliff: **$33,000 → $33** (~18 mo). [E graph-praxis]
- ⚠️ WebNavigator/WebOperator arrive as arxiv HTML with `2603.*` IDs and **no published dates — treat as unverified preprints.**

## Clarion-specific recommendation (synthesis)
1. **Keep Moss as the vector/keyword layer exactly as-is.** Do not try to make Moss hold the graph — it can't, and isn't meant to.
2. **Add a feather-weight, separate site graph** (in-process adjacency keyed by the same `node_id` as the Moss chunks), built deterministically from links + affordances at crawl/observe time. This is the WebNavigator split, minus the heavy infra.
3. **Gate graph use to navigation/path queries only** ("which page hosts this → what's the click path"), keep pure-vector for "what's here." Honors the −9% single-hop penalty.
4. **Grow the graph read-only via the extension relay (lazy, current-page-only)** as the product default; reserve the side Playwright BFS for public/unauthenticated sites. Never CDP-attach the user's primary profile.
5. **Invariant stays intact:** the graph is cross-page *structure* (URLs, link/affordance edges), never live values — `source_node_id`-less, structurally unspeakable, planning-only. Same firewall as today's SITE MAP.

## Next moves
- **One experiment:** on a public multi-page gov flow, build the adjacency sidecar from the existing crawler's `collect_links` output, key nodes to Moss chunk ids, and A\*-path from the landing page to the goal page; compare plan quality vs today's flat SITE-MAP consult. Measure: does the planner pick the right first navigation more often?
- **One follow-up search if we commit:** "networkx vs in-house adjacency for <10k-node in-process graphs persisted as an artifact" + "WebNavigator interaction-graph construction heuristics" (fetch the full paper, not just highlights — it's the blueprint).
- **Decision gate before building:** confirm failures today are *path* failures (planner navigates to the wrong page) not *recall* failures (planner can't find the page). If the latter, improve structure extraction first (graph won't help) — per the atlan threshold rule.

## Sources
### Reddit [R]
- 4 RAG paradigms / multi-hop complexity math — r/Rag — https://www.reddit.com/r/Rag/comments/1ttdh20/i_mapped_out_the_4_fundamentally_different/
- Agentic GraphRAG is a data-modeling problem (ontology-first; Postgres/Mongo for 2-3 hops) — r/AI_Agents — https://www.reddit.com/r/AI_Agents/comments/1t835kx/building_agentic_graphrag_systems_from_knowledge/
- Legal vector+graph "Ferrari to the grocery store" — r/LLMDevs — https://www.reddit.com/r/LLMDevs/comments/1qj2wzf/building_a_legal_rag_vector_graph_am_i/
- GraphRAG scaling skepticism (BM25+dense+RRF baseline) — r/Rag — https://www.reddit.com/r/Rag/comments/1svm8mc/graph_rag_anyone_actually_scaled_it_past_a_few/
- "$500 lesson" gov-portal CDP agent learns DOM/timing — r/AI_Agents — https://www.reddit.com/r/AI_Agents/comments/1nr2h8k/the_500_lesson_government_portals_are_goldmines/
- Browser automation = "architectural debt" — r/AI_Agents — https://www.reddit.com/r/AI_Agents/comments/1od8vv0/openai_just_released_atlas_browser_its_just/
- Indirect prompt injection (11-day exfil) — r/AI_Agents — https://www.reddit.com/r/AI_Agents/comments/1o7xuhf/your_ai_agent_is_already_compromised_and_you_dont/
- Intent vectors vs KGs (STaRK ~60% vs 91%+) — r/LocalLLaMA — https://www.reddit.com/r/LocalLLaMA/comments/1pnah07/intent_vectors_for_ai_search_knowledge_graphs_for/
- "Why we ditched embeddings for KGs" (chunking fragments cause/effect) — r/LLMDevs — https://www.reddit.com/r/LLMDevs/comments/1n3iwrr/why_we_ditched_embeddings_for_knowledge_graphs/
- "Went back to SQL" for agent memory — r/LocalLLaMA — https://www.reddit.com/r/LocalLLaMA/comments/1nkwx12/everyones_trying_vectors_and_graphs_for_ai_memory/
- LEANN 97% storage cut (graph-only, 50-100× slower) — r/vectordatabase — https://www.reddit.com/r/vectordatabase/comments/1qy2ghy/i_investigated_leanns_97_storage_reduction_claim/
- "GraphRAG fixes a real problem" (pro-graph, 228 upvotes) — r/AI_Agents — https://www.reddit.com/r/AI_Agents/comments/1m4rmlz/graphrag_is_fixing_a_real_problem_with_ai_agents/
- "Agentic RAG is mostly hype" — r/AI_Agents — https://www.reddit.com/r/AI_Agents/comments/1ogeb3e/agentic_rag_is_mostly_hype_heres_what_im_seeing/
### YouTube [Y]
- DOM/AXTree as graph, set-of-marks, 68s vs 225s — PY — https://youtu.be/WshRCrMbn8M?t=95
- Observation reps + perceive→act loop, OSWorld multi-app — Berkeley RDI (Caiming Xiong) — https://youtu.be/n__Tim8K2IY?t=1017
- Nodes+edges + A\*/BFS pathfinding, hierarchical planning, symbolic memory — John Tan Chong Min — https://youtu.be/1Yaf6OSCRkk?t=3568
- MemRL: value-select memories that actually worked (+56% ALFWorld) — Emergent Mind — https://youtu.be/wzt2-p9G4Ek?t=1
- Playwright YAML AXTree snapshot, click-by-ref (E45) — RoboticQA Academy — https://youtu.be/IoHPdVSAHpA?t=188
### Exa [E]
- **WebNavigator** (offline interaction-graph + vector index + Retrieve-Reason-Teleport) — arxiv.org/html/2603.20366
- Hybrid vector+graph retrieval patterns (hop knobs, RRF) — medium.com/graph-praxis/hybrid-vector-graph-retrieval-patterns-11fdbd800e3e
- Delegated browser access vs cookie-sync (4 routes; "session is authority") — dev.to/eliofbm/...15p
- CDP root-over-identity risk; never connectOverCDP to user Chrome — debugg.ai/resources/browser-agent-security-risks-cdp-automation...
- GraphRAG +31% multi-hop / −9% single-hop / 9s→22s — medium.com/data-and-beyond/...868078b6d846
- Vector DB vs KG for agent memory (threshold rule) — atlan.com/know/vector-database-vs-knowledge-graph-agent-memory/
- WebDreamer (LLM world model, avoid irreversible actions) — ar5iv.labs.arxiv.org/html/2411.06559
- LASER (web nav as state-space exploration) — arxiv.org/html/2309.08172
- R2D2 (replay-buffer page map, 50% fewer errors) — aclanthology.org/2025.acl-long.1464.pdf
- WMA (transition-focused world model) — arxiv.org/pdf/2410.13232
- "Browser for login, HTTP for data" — dev.to/nirberko/...13oo ; ravoid.com/blog/headless-browser-scraping-architecture
- Plaintext-until-proven crawl tradeoffs — freeman.vc/notes/webcrawling-tradeoffs
- Session bleed survives page.close() — tianpan.co/blog/2026-05-10-browser-agent-session-bleed...
- UnWeaver "VectorRAG is almost enough" — arxiv.org/html/2603.29875v2
- GraphRAG cost cliff $33k→$33 — medium.com/graph-praxis/the-graphrag-cost-cliff
- FalkorDB single-Cypher hybrid — falkordb.com/blog/what-is-hybrid-search-in-ai/
- profiq/ai-web-explorer (site state machine) — github.com/profiq/ai-web-explorer ; project-veil (AXTree→behavior graph) — github.com/0kaman/project-veil
### Moss (capability verification) [E + local SDK]
- Moss is a search runtime, not a database; integrations + roadmap (no graph) — github.com/usemoss/moss ; docs.moss.dev ; moss.dev ; ROADMAP.md
- Context-injection auto-retrieve-per-turn pattern — moss.dev LiveKit integration guide
- MCP server tools (query/load_index/create_index/...) — docs.moss.dev/docs/integrations/mcp-server
- Local SDK introspection: `moss==1.2.0`, MossClient methods are vector/index/doc only, no graph symbols.

## Method notes
- Legs run: A (Reddit, 297 threads), B (YouTube/yt-rag, `yt_web_agent_capture` + full-corpus, 11 chunks), C (Exa, 35 pages). No A/B WebSearch probe (deep dive). + a targeted Moss-graph confirmation pass (SDK introspection + Exa).
- Empty/failed legs: none. Caveat: yt-rag corpus is **thin on explicit web-page-graph world-models** — the best graph source (John Tan Chong Min) came from outside the scoped namespace; treat YouTube as supporting, not primary.
- Date caveat: WebNavigator/WebOperator/UnWeaver are `2603.*` arxiv HTML preprints with unverified dates.
