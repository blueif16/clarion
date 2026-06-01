# Clarion execution architecture — research brief
_scope: ~120d for techniques, all-time for foundational papers • generic AI-agent-engineering lens • deep dive • generated 2026-05-31_
_legs: Reddit + Exa • YouTube skipped (no on-topic yt-rag namespace: corpus is motion-design + algo-trading only) • WebSearch A/B skipped (prior pass established Exa wins technical depth)_

> Purpose: feed the **execution** doc (foundation §11). The product is locked (`docs/foundation.md`). This brief answers *how to build* the four load-bearing pieces: (1) page perception via the accessibility tree, (2) stage / goal-state tracking in LangGraph, (3) voice + web-action without blocking the turn, (4) legibility/trust UX.

---

## TL;DR (highest-confidence, survived ≥2 sources or a primary repo)

1. **Don't feed raw AXTree or raw DOM — feed a *merged, numbered* representation.** The winning pattern (browser-use, agent-browser, cdp tools) fetches `DOM.getDocument` + `Accessibility.getFullAXTree` + `DOMSnapshot.captureSnapshot` **in parallel**, drops occluded/hidden/wrapper nodes, and emits **numbered interactive elements** via a `selector_map`. Token cost ranges 30k–150k (raw HTML) → ~17k (Playwright `ariaSnapshot`) → ~2k/viewport (browser-use) → ~200–3,800 (most compact). For Clarion this is a *double win*: the AXTree is exactly what a screen reader reads (so verifying from it **is** the product), and the numeric index is exactly what you verbalize ("item 5, Submit button"). `[E]`
2. **LangGraph earns its keep *here specifically* because of HITL + stateful stages — the one case even the skeptics concede.** `interrupt()` + `Command(resume=)` IS the consent-gate primitive; the checkpointer IS goal-state persistence; plan-and-execute gives an explicit, *verbalizable* plan. But heed the loud Reddit warning: **one agent walking a stage graph, NOT N agents handing off** ("every handoff is where context dies"; "every added agent = new failure point"; production winners are single-agent). `[E][R]`
3. **Voice stays fluid while the agent acts via LiveKit's `long_running_function` pattern.** Launch the web action with `asyncio.ensure_future`, `await speech_handle.wait_if_not_interrupted([task])`, cancel on barge-in; the tool keeps running in the background even after speech ends. The `hotel-concierge` example is the closest end-to-end blueprint (voice agent → WebRTC RPC → live DOM control, stage/preview state published via participant attributes). `[E]`

---

## What's working (claimed)

**Perception**
- AXTree alone is "flat, verbose, no interaction IDs" — merge it with DOMSnapshot **geometry** + DOM structure, then number the interactables. `[E]`
- `PaintOrderRemover` (drop nodes occluded by overlays/modals) + bbox-containment filter (~99%, so a button's child icon/text don't get separate indices) are the two filters that make the tree LLM-legible. `[E]`
- Structured-tree perception beats pixel-imitation in practice: PokeClaw drives Android via the **accessibility tree** (not screenshots), with deterministic pre-baked "skills" replacing "15 LLM rounds finding the search bar"; OmniParser-style structured parsing ships working agents. `[R]`
- For the **vision fallback** path, Set-of-Marks (overlay bbox + numeric ID on every interactable, reference by ID not coordinates) is the canonical method; WebVoyager found SoM screenshots beat text-only AXTree **59.1% vs 40.1%** — i.e. AXTree-only is weaker on visual sites, so keep Computer-Use as the AXTree-blind fallback. `[E]`

**Stages / goal-state**
- Plan-and-execute (planner emits explicit multi-step plan → executor runs one step/node → re-planner loops) is faster + cheaper than ReAct and forces step-through reasoning you can read aloud. `[E]`
- `interrupt(payload)` persists state via checkpointer, surfaces a JSON payload, resumes on `Command(resume=value)`; `HumanInTheLoopMiddleware(interrupt_on={...})` declaratively gates specific tools with approve/edit/reject/**respond** (respond = perfect for an `ask_user` clarification). `Command(goto=…)` routes stage transitions. `[E]`
- Planner→executor→**verifier** is the practitioner-validated shape; "modular focused agents beat one-agent-for-everything" — but as *nodes/roles*, not separate context-holding agents. `[R]`

**Voice + action**
- `long_running_function.py`: background task + `wait_if_not_interrupted`; `run_ctx.disallow_interruptions()` makes a web action atomic. `[E]`
- **Observer pattern**: a parallel LLM watches the transcript (`conversation_item_added`) and injects guidance via `update_chat_ctx` **without blocking the turn** — a clean home for Clarion's speculative retrieval / background verification. `[E]`
- Background-audio "thinking sounds" + `fast-preresponse.py` (timed filler, cancel-on-response) cover the dead-air while a web action runs. `[E]`
- `hotel-concierge`: 11 typed RPC tools over the WebRTC data channel → widget does DOM control (native-setter form fill + `router.push`), stage/preview state via participant attributes. `[E]`

**Legibility / trust**
- **Morae** (arXiv 2508.21456): an LMM identifies decision points *during* execution and proactively pauses to give BLV users choices — beat OpenAI Operator on task completion + preference match. (Already Clarion's design spine; this is the mechanism citation.) `[E]`
- SAHAY (3-agent voice web agent: Planner/Browser/Voice) flags sensitive steps in the plan and repeat-back-confirms sensitive data. `[E]`
- The numbered AXTree index doubles as the verbalization channel; WebRTC participant attributes are the channel that pushes stage/progress to the on-screen panel (judges/sighted), while spoken readback is the channel for the blind user. `[E]`

---

## What's broken / contested (don't get burned)

- **Browser agents are genuinely flaky.** WebArena best-model success **35.8%**; chaining compounds hallucination. DOM/Playwright/Selenium scraping judged "slow, fragile, expensive," breaks on site updates + bot-detection. Mitigations practitioners trust: structured-tree perception, deterministic pre-baked skills over per-step LLM clicking, record-and-replay + HITL. (For Clarion the **self-hosted demo clone** sidesteps most of this — reliability is a deliberate choice, not luck.) `[R]`
- **Multi-agent over-engineering is the #1 self-own.** "Deleted 400 lines of LangChain for a 20-line while-loop" (−40% latency, can `print(messages)`); "rejected for not using LangGraph" yet bare-metal multi-agent shipped fine; "every handoff is where context dies." Disagreement tracks **task complexity, not framework quality.** Use LangGraph for the HITL/stateful-stage spine; do NOT spawn an agent per stage. `[R]`
- **"Done" is surprisingly hard to define.** A 6-month ops-agent team: defining task-completion "edge cases endless," HITL "not optional," 4 weeks supervised before any autonomy. → Clarion needs an explicit per-stage **done-predicate** + negative verification, not vibes. `[R]`
- **Memory = silent failure.** Unreliable agent memory means "silent forgetting you only catch after damage." → keep goal-state in the LangGraph checkpointer (durable), not in loose LLM context. `[R]`
- **The integration itself is unproven.** No public repo wires browser-use's `selector_map` into a LiveKit tool loop with LangGraph state. This is the build risk — see "Next moves." `[E]`

---

## Numbers worth verifying / citing

- Page-representation token cost: raw HTML **30k–150k**; Playwright `ariaSnapshot` **~17k**; cdp-browser-mcp full-page **~3.8k**; browser-use **~2k/viewport**; agent-browser **~200–300**. `[E]`
- WebVoyager: SoM screenshots **59.1%** vs text-only AXTree **40.1%**. `[E]`
- WebArena: best-model success **35.8%** (the autonomous-web-agent reliability ceiling). `[R]`
- browser-use serializer output cap: **40k chars**. `[E]`
- (From prior brief, still load-bearing: voice turn budget <800ms total; Moss sub-10ms retrieval — verify at sponsor desk.)

---

## Next moves

1. **De-risk the integration spike FIRST (highest unknown):** wire one LiveKit `@function_tool` → a LangGraph subgraph that perceives via a merged-AXTree `selector_map` and returns at an `interrupt()`. Prove the loop: *speak → plan → perceive → propose → (consent) → act → confirm* on ONE field of the self-hosted clone. Everything else is known-good in isolation; only the seam is novel.
2. **Decide the loop ownership** (LiveKit-drives-LangGraph-as-tool vs LangGraph-kernel-with-LiveKit-as-VoiceTransport-port). Foundation §6 says the kernel owns the loop and VoiceTransport is a port → hybrid: LiveKit owns the *real-time voice plane* (STT/turn/barge-in/TTS), LangGraph owns the *task plane* (plan/perceive/propose/consent/act), connected by events.
3. **Adopt AXTree-primary, vision-fallback** — not because AXTree wins benchmarks (it doesn't, 40.1% vs 59.1%) but because reading from the AXTree *is the product's trust claim* (foundation §9: "read from the accessibility tree, not a screenshot, cite the source node"). Use Computer-Use only for AXTree-blind widgets, named honestly.
4. **Per-stage = stage-specialized node (own prompt + tool subset) over shared LangGraph state** — not a separate context-holding agent. Stage transition via `Command(goto=…)`; done-predicate + negative verification per stage.
5. Follow-up search if needed: a dedicated voice-turn-latency pass (subreddits `LocalLLaMA`/`AI_Agents`/`OpenAI`, keyword "voice assistant latency turn-taking realtime") — the Reddit leg came up empty there; Exa covered the LiveKit mechanism but not measured end-to-end loop latency for this exact stack.

---

## Sources

### Reddit `[R]`
- Atlas/Playwright browser automation "slow, fragile, expensive" — https://www.reddit.com/r/AI_Agents/comments/1od8vv0/openai_just_released_atlas_browser_its_just/
- Ex-Manus lead: one `run(cmd)` tool, stuck-detection, exit-code feedback — https://www.reddit.com/r/LocalLLaMA/comments/1rrisqn/i_was_backend_lead_at_manus_after_building_agents/
- PokeClaw: drives phone via accessibility tree + deterministic skills + token-budget stuck-kill — https://www.reddit.com/r/LocalLLaMA/comments/1sdv3lo/pokeclaw_first_working_app_that_uses_gemma_4_to/
- "Deleted 400 lines of LangChain for a 20-line while-loop" (−40% latency) — r/AI_Agents (thread 1owb8yu)
- "Most AI agent startups dead in 12 months": single-agent wins, handoffs kill context — https://www.reddit.com/r/AI_Agents/comments/1s3f2v2/
- "AI Agents: too early, too expensive, too unreliable" (WebArena 35.8%, RPA+HITL) — https://www.reddit.com/r/MachineLearning/comments/1cy1kn9/
- Ops agent 6mo: defining "done" hard, HITL not optional — https://www.reddit.com/r/AI_Agents/comments/1rebqp8/
- OpenClaw ~1000 deploys: memory = silent forgetting — https://www.reddit.com/r/LocalLLaMA/comments/1skce14/
- "Most people don't get LangGraph right" — https://www.reddit.com/r/LangChain/comments/1jvxel4/
- (empty: voice-agent + LiveKit + turn-latency keyword returned only r/Python noise)

### Exa `[E]`
- browser-use DOM serialization pipeline (parallel CDP triple-fetch, selector_map) — https://deepwiki.com/browser-use/browser-use/5.2-dom-serialization-pipeline
- LiveKit `long_running_function.py` — https://github.com/livekit/agents/blob/main/examples/voice_agents/long_running_function.py
- LiveKit `hotel-concierge` (voice → WebRTC RPC → live DOM) — https://github.com/livekit-examples/hotel-concierge
- Morae (arXiv 2508.21456) — https://arxiv.org/abs/2508.21456
- LangGraph interrupts (`interrupt()` + `Command(resume=)`) — https://docs.langchain.com/oss/python/langgraph/interrupts
- LangGraph plan-and-execute — https://blog.langchain.com/planning-agents/
- AXTree token shootout (cdp-browser-mcp) — https://github.com/echo-lumen/cdp-browser-mcp
- WebVoyager (SoM vs AXTree, 59.1/40.1) — arXiv:2401.13919
- VisualWebArena / WebArena (AXTree observation space, SoM) — https://aclanthology.org/2024.acl-long.50
- agent-browser (AXTree → typed PageElement[], page-diff deltas) — https://github.com/malovnik/agent-browser
- SAHAY 3-agent voice web agent — dev.to (2026-03)
- LiveKit observer pattern / background audio / fast-preresponse — https://docs.livekit.io/agents/

## Method notes
- Legs: Reddit + Exa. yt-rag **skipped** (33 namespaces, all motion-design or algo-trading — none cover web agents/voice/LangGraph/accessibility). WebSearch A/B **skipped** (deep dive; prior pass settled Exa's technical-depth edge).
- Complementarity: Reddit supplied the reliability ground-truth + the multi-agent caution and flagged voice-latency as empty; Exa filled that exact gap with the LiveKit non-blocking primitives and the browser-use serialization mechanism. Low echo-chamber risk (different source classes).
- Biggest residual gap: **no source unifies all four pillars** — the LiveKit×LangGraph×AXTree seam is the novel, must-spike part.
