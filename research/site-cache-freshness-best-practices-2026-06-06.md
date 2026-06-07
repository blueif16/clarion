# Site-structure cache: freshness & remember/forget — what shipping products do (and the lightweight subset to borrow)
_scope: 2024–2026, generic (crawlers / browser-agents / agent-memory / test-automation) lens, focused dive • generated 2026-06-06_
_source tags: [R]=Reddit • [E]=Exa web. This is a SECONDARY feature for Clarion (a voice web co-pilot) — goal is the 20% of practices that retain 80% of utility, not a full freshness engine._

## Why this brief
Worry: a cached site map goes stale when the site changes; but rebuilding every visit is costly and throws away accumulated value. Question: how do comparable products solve this, and what's the lightest version we can borrow?

**Headline:** the market has converged on exactly the two-axis model we'd sketched — **freshness = verify/re-derive-on-use (NOT fixed TTL); retention = supersede/decay/consent (NOT hard delete).** Better: the test-automation industry has *productized* "verify-on-use for UI structure" under the name **self-healing locators**, and the caching world has productized "serve-stale-then-refresh" as **stale-while-revalidate**. Both are directly borrowable and cheap.

## TL;DR
- **Don't use a fixed TTL as the correctness mechanism; use verify-on-use.** Practitioners overwhelmingly cache an extraction/structure *and re-derive only when a fingerprint/hash mismatches* — schedule-based full re-scrapes are "wasteful and brittle." [R webscraping/Trawl, CocoIndex; E AgentAtlas] TTL survives only as a coarse safety net. [E sujeet.pro]
- **Stale-while-revalidate (SWR) is the read path for latency-sensitive systems** — serve the cached copy instantly, revalidate async, swap for next time. Perfect for our <800ms voice turn. Canonical config `max-age=1, stale-while-revalidate=59`. [E web.dev / RFC 5861]
- **"Self-healing locators" = verify-on-use for structure, already productized** (Testim/Mabl/Functionize/Healenium; Playwright shipped first-party `Healer` v1.56). Mechanism = multi-attribute element fingerprint + confidence-scored re-match + **fail-loud below threshold**. [E Functionize, Healenium]
- **The make-or-break detail is our invariant.** Practitioners call self-healing "hype" *only when it silently guesses*; it "works" when it's a re-derive gated by a confidence threshold that **says "can't find it" instead of clicking the wrong thing.** [R QualityAssurance; E ScrollTest/Qtrl] That is verbatim Clarion's epistemic clause.
- **Forget by supersession/decay/consent, not deletion.** Mem0 (ADD/UPDATE/DELETE/NOOP at write-time + decay-as-rerank 1.5×/0.3×), Zep/Graphiti (bitemporal `valid_at`/`invalid_at`, keep-but-flag), ChatGPT Atlas (user-controlled, PII-filtered *summaries* not copies, 7-day delete, per-site toggle). "Evict only for compliance." [E Mem0, Zep, OpenAI, Hindsight]

---

## The borrowable lightweight subset (ranked by utility-per-effort)

1. **Verify-on-use, folded into the step you already run.** Before acting on a cached node, cheaply re-check its stored fingerprint against the live page — which is *exactly the re-perceive Clarion already does on arrival*. Don't add a separate refresher; make the existing live perception double as the freshness check. Closest precedent: **AgentAtlas** `validate() → healthy|degraded|stale|failed`, "cache page locator schemas once, reuse at 0 tokens, validate over time." [E github/agentatlas]
2. **Multi-attribute fingerprint per affordance — which we mostly already have.** Store each node as `{role, accessible-name/text, key attributes, reading-order position, parent/section}`. Our merged numbered AXTree → `selector_map` already encodes role+name+position, so we're ~80% to self-healing *by construction*. Borrow the *matching* step: on a non-exact match, score candidates by the fingerprint instead of failing. [E Functionize 5-dim; Mabl ~35 attrs]
3. **Confidence threshold + fail-loud (NEVER silent heal).** Accept the best fingerprint match only if score ≥ ~0.85–0.90; below → surface "I can't find X" (re-plan / ask), never click a guess. This *is* our invariant, and it's the exact line between "works" and "hype." Also: heal-loud — log every repair with confidence for review. [E ScrollTest "≥0.90, alert >5 heals/wk"; Qtrl heal-log schema]
4. **Content-hash adaptive refresh, no config.** Hash each node's affordance-set; on revisit, hash changed → halve its refresh interval, unchanged → double (capped). Self-tunes to each site's churn. Trigger re-derivation on a **success/confidence drop**, not a clock (Trawl re-derives below a **70% success floor**, "no LLM calls after the first one"). [E systemdesigninterview; R Trawl]
5. **Stale-while-revalidate read path** to protect the turn budget: use the cached map *now*, kick the live re-perception in the background, install the fresh version for the next turn. `s-maxage` short + SWR grace. [E web.dev, Fastly]
6. **Supersede + decay, don't delete.** On contradiction, mark the old node superseded and write the new (Mem0 UPDATE/DELETE-at-write); down-rank unused nodes so they still surface if they're the only match (Mem0 decay). Keep superseded for audit (Zep bitemporal). [E Mem0, Zep]
7. **Retention = consent + PII filter + summaries-not-copies.** ChatGPT Atlas is a direct precedent for "no memory without a yes": user-controlled, PII-filtered *summaries* (not full page copies), auto-delete window, per-site visibility toggle, never stores credentials/financial. Maps onto our third invariant + secrets-never-offered. [E OpenAI Help]
8. **TTL only as a backstop + scope keys.** Keep a loose TTL under verify-on-use; namespace nodes by `{site, route, auth_state, locale}` (AgentAtlas scope keys) so variants don't collide; add 10–20% TTL jitter. [E AgentAtlas, sujeet.pro]

## Named patterns & how they work (condensed)

- **Stale-while-revalidate** (RFC 5861; HTTP `Cache-Control`, Vercel SWR, Next.js ISR): within `max-age` fresh; in the `stale-while-revalidate=N` grace window return stale *immediately* + fire ONE async revalidation (kills thundering herd); past it, block. `max-age=1, stale-while-revalidate=59`. Gotcha: only wins for warm/repeated endpoints; a cold entry past the window pays full latency. [E web.dev, httpwg RFC5861, Fastly]
- **Self-healing locators** (mature since ~2018): record-time multi-attribute fingerprint per element; on failure, score every candidate vs the fingerprint, take highest above a confidence cap (Healenium default `score-cap=0.7`; teams push ≥0.90). Functionize adds a reverse-likelihood validator that flags "self-heal validation failed" rather than proceeding. Healing accuracy ~78–94% for id/class renames, ~50–72% for DOM restructure. **Practitioner caveat:** disable on critical paths, alert on every heal, never silent. [E Functionize, Healenium, ScrollTest, Qtrl]
- **Incremental-crawl freshness** (cheap→definitive): `ETag`/`If-None-Match` + `Last-Modified`/`If-Modified-Since` → server `304` no-body (Google: ETag = content hash); sitemap `lastmod` emitted only on *meaning* change; SHA/SimHash content fingerprint to confirm equality; adaptive cadence by observed change rate; tier URLs by volatility. [E Google Search Central, systemdesigninterview, prerender.info]
- **Agent-memory forgetting** (Mem0/Hindsight 4-lever): TTL/age (compliance only), LRU/recency (high-churn), salience (LLM/explicit "remember"), semantic supersession (new contradicts old → replace, decided at write-time by LLM not threshold). Mem0 decay re-ranks at search (1.5×→0.3×, 20 access stamps, never deletes); Zep bitemporal keeps-but-flags. Consensus: "recency-wins-with-explicit-invalidation is the most defensible default; evict only for compliance." [E Mem0, Zep arXiv, Hindsight]

## How this maps to Clarion (the "lighter version")

| Borrow | Clarion home | Effort |
|---|---|---|
| Verify-on-use freshness | Already happens — the live re-perceive on arrival IS the check; just compare to cached fingerprint + update on mismatch | **tiny** (reuse existing perception) |
| Multi-attr fingerprint | `selector_map` already has role+name+position; add a node hash | small |
| Confidence + fail-loud | Already the epistemic invariant ("says when it can't find it") — extend it to cached-node resolution | small, on-philosophy |
| Content-hash adaptive / re-derive-on-failure | New per-node stamp; trigger = the existing done-check / RESCUE failure, not a clock | small |
| SWR read path | Serve cached SITE MAP at PLAN, re-perceive async — protects the <800ms turn | small |
| Supersede + decay | Structure graph nodes + `clarion-task-paths` episodes: update-not-delete | small |
| Consent + PII + summaries | Already the third invariant (`app/remember.py`, secrets-never-offered); Atlas validates the shape | already designed |

**Minimum viable freshness for Clarion = three things, all reusing what exists:** (1) stamp each cached node with a fingerprint hash; (2) on the live re-perceive you already do, compare + supersede on mismatch (verify-on-use); (3) gate cached-node use behind the confidence/fail-loud rule you already enforce. No TTL engine, no scheduled re-crawl, no graph DB. That keeps ~all the utility at near-zero new machinery.

## What's contested / cautions
- **Self-healing is genuinely disappointing when silent.** QA practitioners "still delete and rewrite tests too often"; the deep truth: *"changing one form of DOM address into another doesn't solve that the element mutated — you need a new address."* [R QualityAssurance] → For us this is fine *because* we re-perceive live and only use the cache as a hint; we're not trying to keep a brittle selector alive, we're re-grounding. The lesson is the confidence-gate, not the fingerprint cleverness.
- **A plain "store fact / retrieve fact" vector DB is the wrong abstraction for memory** — it doesn't decay, drift, or forget. [R r/Rag year-long memory build] → reinforces supersede/decay over naive upsert.
- **Most pain is the data pipeline (crawl/clean/freshness), not the model.** [R AI_Agents] → keep the freshness mechanism dead-simple; it's where effort leaks.

## Numbers worth keeping
- SWR: `max-age=1, stale-while-revalidate=59`; API idiom `s-maxage=300, stale-while-revalidate=86400`. [E web.dev]
- Self-heal confidence: Healenium default 0.7; recommended ≥0.90; alert if >5 heals/week; heal adds 3–8s/element; accuracy 78–94% (rename) → 50–72% (restructure). [E ScrollTest, Functionize]
- Trawl re-derive trigger: success-rate < **70%**; cached strategy confidence shown e.g. 0.93; "no LLM calls after the first." [R Trawl]
- Adaptive recrawl: hash-changed → halve interval; unchanged → double (capped). [E systemdesigninterview]
- Mem0 decay: 1.5× recent boost → 0.3× idle floor, 20 access stamps, never deletes. [E Mem0]
- Atlas memory: PII-filtered summaries only, source content deleted post-summary, summaries deleted within 7 days, never stores credentials/financial. [E OpenAI]
- Per-data TTL backstops: static 30–60min; price 1–5min; profile 5–15min; balance = don't cache. [E codesprintpro]

## Closest single precedent to study
**AgentAtlas** (OSS, github.com/bhanuprasadthota/agentatlas) — "browser agents cache page locator schemas once, reuse at 0 tokens, validate over time." `validate() → healthy|degraded|stale|failed`, scope keys `{tenant, device, locale, auth_state, region}`, `run_revalidation_cycle.py`. This is almost exactly our structure-cache + freshness design. ⚠️ Tiny, unproven project — read it as a *design reference / blueprint*, not a battle-tested product.

## Next move
- Spec the **per-node fingerprint stamp + verify-on-use-on-reperceive + confidence-gate** as a small addition to the actuator/planner (no new service). This is the whole "lighter version."
- Decide the one knob: the confidence threshold for accepting a cached-node match before re-perception confirms it (start ~0.85, fail-loud below).

## Sources
### Exa [E]
- AgentAtlas — github.com/bhanuprasadthota/agentatlas
- Self-healing under the hood — functionize.com/blog/self-healing-tests-arent-magic... ; elliot-digital.co.uk/technical-qa/self-healing-tests ; qtrl.ai/blog/self-healing-tests-how-they-work ; scrolltest.com/self-healing-test-selectors-68-percent-fail
- Stale-while-revalidate — web.dev/articles/stale-while-revalidate ; httpwg.org/specs/rfc5861.html ; fastly.com/documentation/guides/concepts/cache/stale
- Incremental crawl — developers.google.cn/search/blog/2024/12/crawling-december-caching ; systemdesigninterview.com/.../812-design-a-web-crawler ; prerender.info/guides/crawl-frequency-signals ; geodocs.dev/technical/http-cache-headers-for-ai-crawlers
- Agent memory — mem0.ai/blog/memory-eviction-and-forgetting-in-ai-agents ; arxiv.org/html/2501.13956 (Zep/Graphiti) ; hindsight.vectorize.io/blog/2026/05/21/agent-memory-consolidation ; help.openai.com/en/articles/12574142 (Atlas)
- Caching fundamentals / TTL — sujeet.pro/articles/caching-fundamentals-and-strategies ; codesprintpro.com/blog/cache-invalidation-patterns
### Reddit [R]
- Trawl self-healing scraper (re-derive on fingerprint mismatch / 70% floor) — r/webscraping 1ro6w0j
- Content-hash incremental refresh — r/webscraping 1q8ndvm ; CocoIndex "always fresh" — r/LLMDevs 1pfi1du ; stateful resume — r/webscraping 1r7ebhp
- Self-healing is hype when silent — r/QualityAssurance 1ltnyym ; "element mutated, need new address" — r/QualityAssurance 1kc5qvq ; DOM-E2E doesn't survive fast products — r/QualityAssurance 1qistft
- Memory isn't a database (decay/reconsolidation/forgetting) — r/Rag 1s19ors ; temporal graph w/ timestamp+confidence+source — r/AI_Agents 1sd7cr5
- "single div class change faceplants the pipeline" — r/AI_Agents 1r2rf2r

## Method notes
- Legs: Exa (37 pages) + Reddit (136 threads). YouTube skipped (corpus thin for this ops topic). Strong cross-leg agreement: verify-on-use > TTL; self-healing works only with a confidence gate; forget by supersession/decay/consent.
- Caveat: AgentAtlas is the only explicit "cache-the-site-structure-and-revalidate" precedent — a design reference, not a proven product. Browser-agent majors document user-fact memory, not structural site-map caching.
