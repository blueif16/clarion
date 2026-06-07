# Plan — confident choices + grounded speech (the de-hardcoded reliability pass)

_authored 2026-06-06 • references the research brief `docs/research/llm-confidence-rag-groundedness-2026-06-06.md` (cited inline as **[brief §X]**) and live code (`file:line`)._
_status: PROPOSAL — awaiting approval. Nothing below P0 is implemented yet._

> One-line thesis: the kernel already says *"the LLM proposes, the kernel + human dispose."* This pass makes the **proposal survivable when it's wrong** — bound the choice by meaning (not strings), attach a calibrated confidence + abstain-when-unsure, and verify a spoken line is *entailed by the page* (not merely plausible). No hard-coded word lists anywhere.

## 0. Demo framing — PREVENTION is the product (reframe, 2026-06-06)
The deliverable is **not** "navigation works." It is **"watch it refuse to corrupt your page / take you to the wrong place, and keep you on track."** The give-up bug was the system *trying* to prevent a wrong move — ungracefully. So the work is to make that refusal **graceful and legible**, and the refusal/abstain path is the **hero of the demo**, not a fallback. This is validated by every major vendor (Operator, Mariner, Computer Use all demo confirm-before-consequence) and by the demo-craft research — see **`docs/research/prevention-demos-showcase-2026-06-06.md`**.

**Re-prioritization that follows:** **P3 (abstain-on-ambiguity)** and **P4 (grounded refusal / verifiable negative)** move from "hardening" to **the star beats**; **P2 (reachability)** is necessary only so the *good* case works under the hero. The demo's four beats (showcase brief §Part 4): grounded read-back incl. a **verifiable negative** → **abstain on ambiguity** (the gov-page "Food assistance vs Food safety" case = P3) → **consent before the consequential step** → **refuse to lead astray**. Add a **recall-on-safe-behavior** metric (Operator-style: % correctly asked/abstained/gated) to the panel so the demo can quote a number. Whitespace Clarion owns: a *spoken verifiable negative* and a *stated grounding/abstain invariant for a blind user* — no surveyed product does either.

---

## 1. Current setup (what exists today)

**Two planes, events not loops.**
- **Voice plane** (`app/voice_entry.py`): LiveKit `AgentSession` (Deepgram STT · MiniMax-M3 via anthropic gateway · LiveKit-Inference TTS) with tools `read_screen` / `advance_task` / `confirm_consent`. `StageGraphRunner` owns the stage graph on thread `"voice-hero"`.
- **Task plane** (`stages/graph.py`): `planner → executor ⇄ replanner`, `rescue` cross-cut. Per subgoal the executor drives the **kernel** (`kernel/graph.py`): `GROUND ▶ VERIFY ▶ PROPOSE ▶ GATE ▶ ⟨consent_gate⟩ ▶ CONSENT ▶ ACT ▶ CONFIRM`, on inner thread `"kernel-<idx>-<uuid>"`.

**The load-bearing mechanisms (and where the choice/grounding happen):**
- **Candidate selection:** `_topk_slice(page, facts, top_k=12)` (`kernel/graph.py:183`) **lexically** ranks 46→12 by word-overlap with grounded *fact values*, then `reasoner.decide_step(goal, slice, …)` (`adapters/openai_reasoner.py:284`) **enum-locks `target_index` to the slice indices** and `reasoner_guard.validate_step_proposal` (`kernel/reasoner_guard.py:38`) fences off-page picks → re-ask → `ReasonerError`.
- **Reversibility:** `irreversibility.classify` (`kernel/irreversibility.py:48`) — dual-signal (model judgement + structural net), escalate-only.
- **Consent routing:** `consent_gate` (`kernel/graph.py:478`) — normal→always consent; fast→reversible auto-proceeds.
- **Invariant enforcement:** `policy.assert_grounded` (epistemic: a `Fact` is speakable iff `source_node_id`), `policy.is_speakable_value` (byte-membership fence #2), `pairing_backs` (#3), `negative_verifier` (#5), `policy.assert_consented` (agentic).
- **Done-check:** `stages/checks.evaluate_success_check` — code verifies the page-state effect (e.g. `navigated` = URL changed).

**Changes already made THIS session (P0 — keep):**
- `kernel/irreversibility.classify`: a `read` action is `reversible` by construction (fixes the crash) + regression test. ✅
- `app/voice_entry.py`: live default `mode` `fast → normal` (×2 call sites). ✅
- `CLAUDE.md`: added the "NEVER hard-code word/keyword lists … use the LLM/embeddings behind a port" rule. ✅
- Reverted the hard-coded `_GOAL_STOPWORDS` slice patch (back to original lexical `_topk_slice`). ✅

---

## 2. The failures (diagnosed, ordered by severity)

| # | Failure | Root cause | Status | Evidence / refs |
|---|---|---|---|---|
| **F1** | `PolicyViolation` crash on the gov page | a degraded **read-back** inherited the model's "irreversible" judgement of the *abandoned* navigate; `consent_gate` sent the read to ACT; `assert_consented` fired | **FIXED** (P0) | `kernel/graph.py:587`, `policy.py:207`; worker log 19:23 |
| **F2** | "I wasn't able to navigate… give up" | **lexical** `_topk_slice` ranks by string-overlap with KB facts (not the goal); the "Food assistance" link (~position 17 of 46) is pruned out of the 12-node slice; `decide_step` enum-locks to the slice → target **untargetable** → read-back → `navigated` fails → replan → give up | **OPEN** | `kernel/graph.py:183/321`, `openai_reasoner.py:294/305`; WebFetch of usa.gov/benefits; **[brief §How-it-failed]** |
| **F3** | The choice is trusted with no confidence; silent auto-proceed | the LLM's single pick is acted on; fast mode auto-proceeded reversible steps with no spoken checkpoint | **PARTIAL** (mode→normal done; no confidence/abstain yet) | `consent_gate` `kernel/graph.py:505`; **[brief Part 1, Rec A]** |
| **F4** | Groundedness is structural-only | the epistemic clause checks `source_node_id` + byte-membership, but there is **no entailment check** that a *synthesized* / negative spoken line is actually supported by the perceived region | **OPEN** | `policy.is_speakable_value`; **[brief Part 2, Rec B; FRANQ]** |
| **F5** | Debugging blind | kernel/executor `trace` lives in state but is **not logged**; only voice-layer lines reach the worker log | **OPEN** | this session took 3 round-trips for lack of it |

---

## 3. What to fix / edit / improve (work items)

Each item: **what · where · why (+ref) · how (kernel stays SDK-free) · risk · test.**

### P1 — Observability: surface the kernel/executor trace to the worker log
- **What/Where:** in `StageGraphRunner` (`app/voice_entry.py`) log each *new* `trace` event after `advance()`/`resume()` (decide_ms, chosen target+name, GATE classification, EXECUTOR done + success_check, REPLANNER gave_up).
- **Why:** F5. Cannot verify P2–P4 without seeing the decision path; this is the gap that made this session slow. App-layer only — no kernel change, no purity risk.
- **Risk:** trivial; bounded by ~10–30 events/run; truncate lines.
- **Test:** run the gov-page case; confirm `PROPOSE`/`GATE`/`EXECUTOR` lines appear.

### P2 — Fix the give-up: stop selecting candidates by string-matching
Two-stage, smallest-safe-first:

**P2a (immediate) — let the LLM decide over the full interactive map.**
- **What/Where:** in `PROPOSE` (`kernel/graph.py:321`) pass the full `page` (or a high cap) to `decide_step` instead of the lexical 12-slice, so `target_index` can be **any** live control. Keep `reasoner_guard` (it already fences hallucinated indices).
- **Why:** F2; the LLM is the semantic decider — removing the dumb pre-filter both fixes the bug and honors the new no-hardcode rule **[brief §Part 1 — "for action selection, dispersion + retrieval-compatibility, not a lexical pre-rank"]**.
- **Risk:** `decide_step` latency on ~46 short node-lines (prefill, not decode). Measure with P1; the "4.7s baseline" was Gemini-with-thinking, not M3-thinking-off.
- **Test:** gov-page — target index now in the enum; `navigated` passes after consent.

**P2b (hardening) — replace the lexical rank with a semantic `ContextRanker`.**
- **What/Where:** introduce the ranker the contract already names (`Reasoner.decide_step` doc: *"the top-K slice the **ContextRanker** pre-ranked"*, `contracts/ports.py:106`). Rank candidate controls by **embedding similarity (goal ⇄ control name+role)**, **recall-oriented** (generous N, e.g. ~half the map — never prunes the target). Injected as an optional dependency into `build_kernel`/`build_stage_graph`; **default = full-map (P2a)** when none is injected, so offline/tests need no embedder and there is zero lexical heuristic left.
- **Why:** F2 + safety — a meaning-bounded candidate set shrinks the surface (an unrelated "Click to win" / "Sign in" control scores low and is dropped) **[brief Rec A; "fuse … retrieval-compatibility"]**. The similarity score is *reused* as a confidence signal in P3.
- **How (purity):** kernel imports no SDK — it calls an injected `rank(goal, page) -> SelectorMap` (a Protocol/callable). The embedder lives in `adapters/`/`retrieval/`. **Design decision to confirm:** add it as a duck-typed injected callable (no change to FROZEN `contracts/`) vs. a formal port. Recommend duck-typed injection first.
- **Risk / open question:** **embedder speed** — `moss-minilm` embeds *inside* Moss search (not exposed standalone); the Gemini `embed()` path is ~2.7s/call (too slow per step). Must either expose a fast local embedder or keep P2a as the shipping default. **This is the gating decision for P2b/P4.**
- **Test:** a page where the goal-relevant control sits past the lexical cutoff is still ranked into the candidate set.

### P3 — Confident choice + abstain-on-ambiguity (the safety answer)
- **What/Where:** wrap `decide_step` so the choice carries a **confidence** and can **abstain**:
  1. **self-consistency dispersion** — sample `decide_step` N=2–3× (tiered: only when needed); divergent picks ⇒ low confidence **[brief: CISC 2502.06233, VCSC]**;
  2. **goal↔control similarity margin** — top-1 vs top-2 similarity from P2b **[brief: UniCR 2509.01455]**;
  3. if low-confidence / ambiguous → **emit a read-back-and-ask proposal** ("I can see *Food assistance* and *Food safety* — which did you mean?") — a safe `read` action, never a guess **[brief: I-CALM abstention-as-first-class; Rec A]**.
- **Why:** F3 — answers "how do we make the choice safer without re-hardcoding": the LLM still decides, but from a meaning-bounded set, with a calibrated gate, and it abstains rather than mis-picks. The consent readback naming the grounded control remains the conformal backstop.
- **How (purity):** sampling = call the injected reasoner N times (no SDK); similarity from the injected ranker; the abstain proposal is built in PROPOSE exactly like the existing degraded read-back.
- **Risk:** N samples add latency — mitigate by sampling **only** when the single-shot margin is thin (tiered, **[brief §Latency tax]**).
- **Test:** an ambiguous two-candidate page → abstains and asks; a clear page → acts after one sample.

### P4 — Grounded speech: claim-level entailment for synthesized/negative lines (tiered)
- **What/Where:** before speaking any **synthesized** or **negative** line (not a verbatim membership read-back), run a Tier-1 entailment check that the claim is supported by the perceived region; if not entailed → **hedge/abstain** ("I couldn't confirm that from what's on the page"). Extends the existing `negative_verifier` pattern to a general grounder.
- **Why:** F4 — upgrades the epistemic clause from *structural membership* to *entailment* **[brief Part 2: RAGAS faithfulness, SelfCheckGPT-NLI, Vectara HHEM]**. **FRANQ [brief]** is the key reference: keep **faithfulness (supported-by-page)**, not factuality — and beware that retrieval presence inflates model confidence. Map **one spoken `Fact` = one atomic claim with a non-null supporting node**.
- **How (purity):** a `Grounder` injected dependency; impl can be a **local NLI / HHEM-2.1-Open** (no LLM-judge round-trip) in `adapters/`. Kernel calls `grounder.entails(claim, context) -> bool/score`.
- **Risk:** latency — **tiered**: plain verbatim reads skip it (already fenced #2); only synthesized/negative lines pay. Same embedder/model-host question as P2b.
- **Test:** a synthesized claim unsupported by the region → hedged; a plain sourced read → unaffected.

### P5 — Keep: navigations always get a spoken checkpoint
- Covered by the normal-mode default (P0). Document the rule so that if fast mode is re-enabled, a **navigation** (you-leave-this-page) still gets the consent readback **[brief Rec A; prior safety discussion]**.

---

## 4. Sequencing (DAG) & what's on the demo critical path

```
P0 (done) ─┐
P1 logging ─┼─▶ verify everything
P2a full-map ──▶ (give-up FIXED, observable)          ← demo critical path
P2b semantic ranker ──┐  (needs embedder-speed decision)
                       ├─▶ P3 confidence+abstain (uses similarity; can start dispersion-only)
P4 grounder (NLI/HHEM) ┘  (independent; needs model-host decision)
P5 doc rule (done via P0)
```
- **Ship now (low risk, high value):** P1 + P2a → navigation works and is debuggable.
- **Then safety:** P3 abstain-on-ambiguity (dispersion-only works without the embedder).
- **Then hardening:** P2b semantic ranker + P4 grounder — both gated on the **embedder/NLI host** decision.

---

## 5. Open decisions (need your call)
1. **P2 shape:** ship **P2a (full-map)** as the default and add **P2b (semantic ranker)** as hardening — or go straight to the embedding ranker? (Recommend P2a now, P2b after #2.)
2. **Embedder/NLI host:** is there a fast **local** embedder/NLI we can expose (for P2b similarity + P4 entailment), or do we accept full-map + skip per-step embedding until then? `moss-minilm` isn't standalone; Gemini embed ≈ 2.7s/call is too slow per step.
3. **Contract boundary:** add `ContextRanker`/`Grounder` as **duck-typed injected callables** (no change to FROZEN `contracts/`) — confirm that's preferred over formal ABCs.
4. **Latency budget:** acceptable N for P3 sampling and whether P4 runs inline or only pre-irreversible (the tiered cutoff).

## 6. Verification (per item)
- Unit tests beside existing suites (`kernel/tests`, `stages/tests`): P2 target-reachability; P3 abstain-on-ambiguity + act-on-clear; P4 entail-pass/hedge-fail; all keep the 191-test gate green.
- Live: one gov-page run with P1 logging — confirm PROPOSE targets "Food assistance", GATE→consent, ACT→`navigated`=true.
- `python scripts/copy_lint.py` on any new voice copy (banned words).

## 7. References
- **Brief (methods):** `docs/research/llm-confidence-rag-groundedness-2026-06-06.md` — Part 1 (confidence: UniCR 2509.01455, CISC 2502.06233, VCSC, I-CALM), Part 2 (groundedness: RAGAS, SelfCheckGPT-NLI, Vectara HHEM, HALT-RAG 2509.07475, **FRANQ 2505.21072**), §Latency tax (tiered), Rec A/B/C, §Ready-to-paste scaffolds.
- **Brief (demo/showcases):** `docs/research/prevention-demos-showcase-2026-06-06.md` — what Operator/Mariner/Computer-Use/PageGuide demoed for prevention, the demo-craft principles, the four-beat Clarion narrative, and the verifiable-negative/stated-invariant whitespace.
- **Code:** `kernel/graph.py` (`_topk_slice:183`, `propose:295`, `consent_gate:478`, `act:547`), `kernel/irreversibility.py:48`, `kernel/policy.py`, `kernel/reasoner_guard.py:38`, `stages/graph.py`, `stages/checks.py`, `adapters/openai_reasoner.py:284`, `contracts/ports.py:93` (the `ContextRanker` the doc already names).
- **Invariant:** `CLAUDE.md` — "no fact without a source, no action without a yes"; the new no-hardcode rule.

---

## 8. Execution status (2026-06-06)
Spawned one subagent per clear task; verified each independently by re-running the full no-network gate.

| Task | Subagent | Files | Verify | Status |
|---|---|---|---|---|
| **P0** crash fix + mode→normal + CLAUDE.md rule | (main, earlier) | `kernel/irreversibility.py` (+test), `app/voice_entry.py`, `CLAUDE.md` | gate green | ✅ done |
| **P1** trace logging | subagent-A | `app/voice_entry.py` (`StageGraphRunner._log_trace`, whitelist `_TRACE_KEEP`, called in advance/resume) | gate 191 green; parses | ✅ done |
| **P2a** full-map candidates | subagent-B | `kernel/graph.py` `propose` → `decide_step(goal, page, …)`; `_topk_slice` retained but off-path (superseded note) | gate 191 green; no test weakened | ✅ done |
| **P3** abstain-and-clarify (hero beat) | subagent-C | `contracts/state.py` (`StepProposal.alternatives=[]`, additive), `adapters/gemini_reasoner.py` + `openai_reasoner.py` (schema/decode/prompt self-report), `kernel/graph.py` `propose` `(2b)` block, 2 new tests | gate **193** green; additive default `[]` confirmed; copy_lint PASS | ✅ done |
| **P2b** semantic ContextRanker | — | — | **deferred — needs embedder-host decision** | ⛔ blocked |
| **P4** entailment grounder | — | — | **deferred — needs NLI-host decision** | ⛔ blocked |

**Net:** the gov-page give-up is fixed (P2a: target now reachable), and the ambiguous case now ABSTAINS with a spoken "which did you mean?" (P3 hero beat). All consequential steps gate at CONSENT (P0 normal default). Decisions are observable in the worker log (P1). Gate: **193 passed, 10 deselected.**

**Not committed** — all of P0–P3 + the two research briefs + this plan are uncommitted on `main`. Recommend a feature branch + conventional commits before the next live run (CLAUDE.md: branch only, don't push unless asked).

**Next:** (1) live gov-page run with P1 logging to confirm PROPOSE targets "Food assistance" and the ambiguous "Food assistance vs Food safety" abstains; (2) answer the embedder/NLI-host question to unblock P2b/P4; (3) add the recall-on-safe-behavior metric to the panel (demo).
