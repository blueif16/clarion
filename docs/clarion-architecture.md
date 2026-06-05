# Clarion — Target Architecture (LLM-as-decider, generic)

_Generated 2026-06-04 from a multi-agent research+design workflow (19 agents: frame → 5
research angles → 4 competing designs → 8 adversarial evals → synthesis). This is the
**proposed** post-hardcoding architecture — the decision artifact, editable. Supersedes the
hardcoded pay topology. Read with `docs/foundation.md` (product) + `docs/clarion-status.md`._

> **STATUS (2026-06-05): SHIPPED.** All four de-hardcoding systems + the de-hardcoded
> spine are implemented and committed on `feat/clarion-extension` (Steps 0–5 + 7), and
> proven end-to-end on two real gov sites (usa.gov + weather.gov) with zero site-specific
> code — see `docs/clarion-status.md`. Default Reasoner = `GeminiReasoner` (`thinking_budget=0`,
> ~2s decode) with a Qwen/Nebius failover. **Deferred:** Step 6 (SpeculationController/
> DeliveryGate — the <800ms voice-turn latency layer) and the actuator AX enrichment for the
> structural gate. The residuals below stand as written.

## Thesis — **Clarion-PE/G** (Planner/Executor over a frozen Reasoner port)

> One generic LLM **reasons** the plan and the next grounded action behind a frozen `Reasoner`
> port; a deterministic LangGraph kernel **acts**, **enforces** the two invariants in code
> (verbatim + *paired*-grounded speech, dual-signal fail-closed consent, page-verified done),
> and **overlaps** the model call under the spoken turn. De-hardcoded **by deletion**, fenced so
> the model can only point at real, correctly-paired nodes, and never auto-acts an irreversible step.

The Planner/Executor split won unanimously across all 8 evals (cleanest de-hardcoding, most
surgical migration, kernel gates untouched, mockable for deterministic CI). The synthesis grafts
the runner-up fixes that close the killers every eval converged on.

## The three killer-closers (why this beats a naive LLM loop)

1. **Grounded-but-MISPAIRED value** (the worst epistemic failure: a clean citation on the wrong
   number — e.g. reads the past-due row's `$142.10` as the amount due). → **`PairedFact`**: a
   label↔value pairing is a first-class grounded Fact done **geometrically** at extract time
   (shared-row bbox / `aria-labelledby` / `for` / DOM ancestry — NOT 8px reading-order). An "X is
   Y" sentence may be spoken **only if a single PairedFact backs both halves**; a mis-pairing is
   ungroundable and refused.
2. **Confidently-wrong IRREVERSIBILITY** (a benignly-named "Continue" that submits). → **dual-signal
   `{reversible | irreversible | UNKNOWN}` gate**: the model judges from grounded context AND an
   independent **code structural pre-screen** runs at the gate (`role=button` AND `type=submit` /
   inside a `<form>` / off-origin nav / empty accessible name). **Either signal can escalate; the
   model can never downgrade past the structural net.** UNKNOWN routes through CONSENT even in Fast
   mode. The closed keyword list (`pay/submit/confirm/send`) is **deleted**.
3. **Actor = its own done-judge** (model self-grades success). → **done is a code SELECTION**: the
   Reasoner *selects* a registered generic check (field-now-nonempty / node-added / error-absent /
   navigated / grounded-confirmation-Fact-appeared); CODE evaluates it against the freshly
   re-perceived tree (`diff_maps` + a semantic anchor like a URL change or grounded status Fact).
   A step advances only on a real page-state check — never the model's say-so.

## Components (the kernel keeps only invariants + consent; the LLM decides)

- **VoicePlane / Fast-Talker** (KEEP verbatim) — LiveKit + Deepgram + Gemini LLM/TTS; owns the
  <800ms turn, ORIENT, goal restate+confirm, speaks the consent readback. **Speaks ONLY
  pre-composed grounded strings** handed up via `ConsentRequest` — no independent channel to a page
  fact.
- **`Reasoner` port** (NEW frozen ABC, the de-hardcoding boundary; keeps `kernel/`+`contracts/`
  SDK-free, mockable) — `plan_goal(goal, orient, affordances) -> [Subgoal]` and
  `decide_step(goal, ranked_slice, facts, history) -> StepProposal`.
- **`GeminiReasoner` adapter** (NEW, the ONLY new LLM home) — Gemini structured output; emits
  `{scratch_reasoning (drafted first), action_kind, target_index (validated vs live map), value_ref
  (validated vs Fact ids | null), irreversible+rationale, success_check (a SELECTION), say
  (verbatim from grounded spans)}`. Off-page indices/values caught by **code-side post-decode
  validation + reject** (structured output ≠ logit mask).
- **PARSE / ContextRanker** (KEEP & EXTEND `page_retriever._score` + `pipeline.py`; **parallel HINT,
  never decider**) — pre-ranks the numbered map into a top-K label-paired candidate slice, harvests
  control-values + `aria-live`, stops deduping value-bearing facts, runs a regex irreversibility
  pre-screen that can only **escalate**. Every output is overrideable; an unfiltered fallback runs
  before any honest-decline so over-pruning can't cause a false give-up.
- **`PairedFact` + `Fact.id`** (NEW contract additions, additive) — stable content+nodeId id so
  `value_ref` is an enum over real ids; pairing encodes both label + value node.
- **PLAN / GROUND / VERIFY / PROPOSE / IrreversibilityGate / consent_gate / CONSENT / ACT /
  VERIFY-DIFF** kernel nodes — see migration. GROUND→VERIFY epistemic chokepoint kept; **VERIFY adds
  set-membership** (the spoken value must be byte-identical to a Fact currently in `grounded_facts`,
  upgrading the hollow `source_node_id != None` check at `policy.py:50`). consent/ACT/once-flag KEPT
  verbatim.
- **SpeculationController + DeliveryGate** (NEW, behind a flag, lands LAST) — `on_partial` pre-fire
  perceive+embed+speculative decide against an AXTree-hash snapshot; DeliveryGate does a **cheap
  target-node-only** freshness re-check between "yes" and act so a stale index is discarded+replanned,
  never clicked.
- **NegativeVerifier** (NEW, deterministic, ~ms) — a spoken negative ("no late fee") comes ONLY from
  a closed-world search over `grounded_facts` finding no asserting node **AND coverage evidence**;
  else it **downgrades to a hedge**. Drives the honest decline, distinguishing "not afforded" from
  "couldn't perceive."

## Memory = the lean `ClarionState` checkpoint (one source of truth)

No second world-model, no drifting LLM scratch. Node ids + grounded Fact **values** + SelectorMap +
generic plan + pending Proposal + audit channels. **Raw AXTree/HTML never serialized** (re-fetched
in-node → sub-15ms writes; a CI test fails if a SelectorMap/AXTree lands in the checkpoint). Reducer
discipline unchanged (`trace`/`consent_log` = `operator.add`, nodes return only new entries). Plan
keys live on a `_PlanState(ClarionState, total=False)` superset (the existing `_StageState` pattern)
so `contracts/` stays frozen; only additive contract changes are `Fact.id` + `PairedFact`.

## The two invariants, enforced in code

- **Epistemic** — five stacked fences, cheapest-first, all fail-closed: (1) extract-don't-generate
  (value is a verbatim substring of a grounded Fact); (2) **membership** in live `grounded_facts`;
  (3) **pairing-correctness** (PairedFact); (4) `assert_grounded` at VERIFY unchanged chokepoint;
  (5) negatives only from NegativeVerifier with coverage, else hedge. The Fast-Talker has no channel
  to originate a fact.
- **Agentic** — unchanged at the contract level (`interrupt()`/`Command(resume)`, ACT once-flag,
  `assert_consented` hard-stop). Only the **trigger** generalizes: the dual-signal gate above. Fires
  on ANY consequential control on ANY site.

## Migration path (strangler; validate each step by BEHAVIOR on a REAL site, `load_dotenv` real keys)

0. **Instrument FIRST** — extend `instrument/timed.py` with `perceive_ms`/`decide_ms`/`stale_check_ms`/
   `turn_ms` + a mock-Reasoner CI harness. **Validate:** run the extension actuator's `perceive()` on
   a real heavy page; confirm the serial per-node stamp loop (`extension_actuator.py:131-132`) is the
   dominant cost (expect >150ms). _This number gates everything._
1. **Make `perceive()` cheap** — batch the per-node `data-clarion-id` stamp into ONE CDP call (or
   stamp lazily only the target); add a target-node-only incremental re-perceive. **Validate:**
   `perceive_ms` drops an order of magnitude (60 round-trips → 1–2).
2. **Add the `Reasoner` port + `GeminiReasoner` + `Fact.id`/`PairedFact`** behind a mock. **Validate:**
   a 20-line spike on a real page's map+facts — `decide_step` returns an existing index, a resolvable
   `value_ref`, and rejects an off-page index.
3. **Replace `plan_goal` + PROPOSE's name-match decider with the Reasoner** (delete `_hero_plan`,
   `graph.py:171`, `kernel/graph.py:189-196`). **Validate:** Normal mode, TWO goals on TWO real sites
   (a usa.gov benefits-status lookup AND a form/unsubscribe), ZERO site-specific code — states a
   goal-derived plan, reads a crisp paired value with a citation, gates every step.
4. **Replace the done-predicate registry with the SELECTED generic check + semantic anchor** (delete
   `predicates.py` done registry + marker lists; keep `detect_rescue`). **Validate:** on a real SPA
   form a no-op step is detected as failed-not-advanced; a read-only lookup certifies via the anchor.
5. **Land NegativeVerifier + honest-decline + UNKNOWN-gates-Fast-mode** + cap Fast to one reversible
   act before a spoken progress beat. **Validate:** a charge rendered as an image → the agent HEDGES,
   not a confident "no late fee."
6. **Land SpeculationController + DeliveryGate behind a flag** (latency layer LAST). **Validate:** a
   self-correcting goal on a real SPA stays <800ms warm, the pivot discards stale speculation, the
   gate doesn't thrash on a benign poll re-render.
7. **Rewrite the hero tests as the generic spec** (red-before-green) — delete the 52+ assertions
   pinning the AUTH→…→CONFIRM topology; add invariant tests (ungrounded/mispaired/uncovered → refused;
   model-reversible submit still can't reach ACT in Fast; truncated harvest forbids a confident
   negative; no-op doesn't advance). Mock the Reasoner so they stay network-free.

## Open risks (honest residuals)

- **Confident-false-reversible** on a structurally-invisible JS control (div `role=button`, no form,
  no nav) the model reads as reversible — reduced by UNKNOWN-on-no-grounded-undo (which over-gates),
  not eliminated. The single worst residual; unprovable on an unseen site.
- **Grounded-but-wrong-TARGET** (right value, wrong field) survives the value fence; caught only if
  the page-diff shows a no-op. No independent second verifier in the hot path (single-decider, for
  latency) — by design; the catch is bounded replan + surfaced low confidence.
- **AX-tree lossiness** (95.9% of real sites have a11y errors): a value in canvas/SVG/image is
  invisible; `perceive_vision` is deferred → a choked control yields an honest "I can't read that,"
  a dead-end until a vision-relabel RESCUE exists.
- **Speculation is a statistical bet** (~45–55% warm-hit); the cold path stacks perceive+decide with
  no sentence to hide under → degrades to slow-but-honest, never a guess.
- **Migration churn is larger than "surgical"** — a multi-day refactor; a rushed test rewrite could
  silently weaken an invariant. Red-before-green is the only guard.

## Next research (before committing the latency layer)

Measure real per-step latency end-to-end on the extension transport after batching the stamp;
validate batched-stamp correctness + incremental re-perceive; prototype the success-check SELECTION
DSL on 2 real goals; spike Gemini structured output with a per-call enum of 50+ live ids (TTFT,
schema honoring); tune the DeliveryGate settling detector on aggressive SPAs; scope the vision-relabel
RESCUE; calibrate the UNKNOWN threshold for confirmation fatigue.
