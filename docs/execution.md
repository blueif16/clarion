# Clarion — Execution Plan

_generated 2026-05-31 • depends on `docs/foundation.md` (product, LOCKED) + `research/clarion-execution-architecture-2026-05-31.md` (technical evidence) • status: build spec for the YC Conversational AI Hackathon, June 6–7 2026_

> This doc turns the locked product into a buildable system. It does **not** relitigate scope, audience, demo, or the invariant — see `foundation.md`. It answers the four questions foundation §11 deferred: **the a11y-tree actuator, the stage machine, the voice seam, and instrumentation/recording.**

---

## 0. The one thing that matters: the seam

Three of the four pillars are known-good in isolation:
- **Perception** → browser-use-style merged AXTree (proven).
- **Voice** → LiveKit real-time loop (proven).
- **Stateful stages + consent** → LangGraph `interrupt()`/checkpointer (proven).

**What no public repo ships is the *seam*:** a merged-AXTree `selector_map` wired into a LiveKit tool loop with LangGraph state. That seam is our only novel engineering risk. **Execution de-risks the seam first** (§7 spike), then fills in the known-good parts around it. Everything below serves that ordering.

---

## 1. Decided architecture — two planes, one event bus

(Decisions locked 2026-05-31: loop ownership = two planes; stage model = specialized nodes over shared state. See `research/…` for the evidence trail.)

```
┌─ VOICE PLANE (LiveKit) ────────────────────────────────────┐
│  STT · semantic turn-detect · barge-in · TTS · filler audio │
│  owns the <800ms turn budget — never reimplemented elsewhere │
└───────┬───────────────────────────────────▲────────────────┘
        │ @function_tool advance_task()      │ interrupt payload
        │ (kicks the graph, non-blocking)    │ (PROPOSE → speak it)
        ▼                                     │  Command(resume=yes/no)
┌─ TASK PLANE (LangGraph kernel) ─────────────────────────────┐
│  intent ▶ GROUND ▶ VERIFY ▶ PROPOSE ▶⟨CONSENT⟩▶ ACT ▶ CONFIRM │
│  checkpointer = durable goal-state   interrupt() = consent   │
│  observer (parallel) = speculative retrieval + verification  │
└───────┬─────────────────────────────────────────────────────┘
        ▼  Actuator port
┌─ A11Y-TREE ACTUATOR (Playwright/CDP) ───────────────────────┐
│  merged numbered AXTree → selector_map → act → observe      │
└─────────────────────────────────────────────────────────────┘
```

**The contract between planes is events, not nested loops.** The voice agent never blocks on the task graph; the task graph never owns the microphone. This is what keeps the conversation fluid while the agent acts (foundation §3's "drives the task" without dead air).

**The kernel still imports zero provider SDKs** (foundation §6 invariant). LiveKit/Moss/Minimax/Playwright all sit behind ports; the kernel sees only the loop, the two-clause policy, the two modes, and the trace.

---

## 2. The kernel as a LangGraph graph

### 2.1 Shared state (the durable goal-state — lives in the checkpointer, NOT loose LLM context)

```python
class ClarionState(TypedDict):
    goal: str                       # "pay my electric bill"
    mode: Literal["normal","fast"]  # autonomy slider (foundation §5)
    plan: list[Stage]               # explicit, verbalizable (planner output)
    stage_idx: int                  # which stage we're in
    step: tuple[int,int]            # (k, n) within stage → "2 fields left"
    page_index: SelectorMap         # current merged-AXTree (§4)
    grounded_facts: list[Fact]      # {value, source_node_id, retrieved_at}
    pending_proposal: Proposal|None # what we're about to do/say
    consent_log: list[Consent]      # audit trail for the glass-box trace
    trace: list[TraceEvent]         # every node entry/exit → demo UI
```

Why durable: Reddit's clearest reliability lesson is *"unreliable memory = silent forgetting you only catch after damage."* Goal-state in the checkpointer (AsyncPostgresSaver in anything beyond the spike) is survivable across an `interrupt()`; loose context is not.

### 2.2 The loop nodes (one per kernel verb)

| Node | Does | Reads | Writes | Port |
|---|---|---|---|---|
| `GROUND` | retrieve the goal-relevant fact(s) for this step | goal, page_index | grounded_facts | Retriever (Moss) |
| `VERIFY` | assert only grounded facts, **including negatives** ("no late fee [verified: not present]") | grounded_facts, page_index | grounded_facts (verified flag) | — |
| `PROPOSE` | form the next spoken action ("fill card field with •••; say yes to continue") | grounded_facts, step | pending_proposal | — |
| `⟨CONSENT⟩` | `interrupt()` → surface proposal to the voice plane → wait for `Command(resume)` | pending_proposal, mode | consent_log | (HITL) |
| `ACT` | execute the approved action via the actuator | pending_proposal, page_index | page_index (post-action) | Actuator |
| `CONFIRM` | re-perceive, run the **done-predicate**, detect silent failure, write-back | page_index | step/stage_idx, trace | Memory |

### 2.3 Consent gate = `interrupt()` (the agentic clause, mechanized)

- **Normal mode:** every consequential step hits `⟨CONSENT⟩`. `HumanInTheLoopMiddleware(interrupt_on={...})` declares which tools require a yes; decisions = approve / edit / reject / **respond** (respond routes an `ask_user` clarification back through the voice plane).
- **Fast mode:** reversible nodes (GROUND/VERIFY/PROPOSE navigate+read+fill) auto-resume; the gate is *armed only* at the irreversible step (the foundation §5 hard-stop). Implement as a `mode`-conditional edge into `⟨CONSENT⟩`.
- **Idempotency gotcha (load-bearing):** on `Command(resume=)` the interrupted node **re-executes from the top**. Any side-effect *before* the `interrupt()` (e.g. an actuator click) must be idempotent or guarded by a `consent_log` check. This is the #1 way the consent gate silently double-acts — bake it into the ACT node, not bolt it on.

---

## 3. Stages — specialized nodes over shared state

**Not agents-per-stage.** One agent/context walks a **stage graph**; each stage is a *specialized node* (own system prompt + tool subset + done-predicate + negative verification). Transitions via `Command(goto=next_stage)`. This keeps the legible stage-tracking foundation §3 demands while dodging the *"every handoff is where context dies"* failure mode (the single loudest practitioner warning).

### 3.1 The plan-and-execute spine

`planner` node emits the explicit `plan: list[Stage]` (read aloud verbatim → instant legibility) → each stage node runs the §2.2 loop scoped to its job → `replanner` revises when CONFIRM's done-predicate fails or the page surprises us. Plan-and-execute over ReAct: cheaper, faster, and the plan is the thing you verbalize.

### 3.2 The hero task's stages ("pay my electric bill")

| Stage | Tool subset | Done-predicate | Negative verification |
|---|---|---|---|
| `AUTH` | navigate, read, fill-credential | logged-in marker present in AXTree | "no error banner; no 'locked' state" |
| `LOCATE` | navigate, read, retrieve | amount + payee + due-date all grounded with source nodes | "no autopay already-scheduled state we'd duplicate" |
| `FILL` | read, fill-field (native-setter) | all required goal-fields populated | "**no required field left blank**"; "no silent validation error" (the one the screen reader never announced) |
| `REVIEW` | read, retrieve, cross-check | amount matches known balance; payee matches expected | "no surprise fee/upsell added to total" |
| `⟨PAY⟩` | submit | — (this is the gate) | confirmation number present post-act |
| `CONFIRM` | read, write-back | success marker + confirmation # grounded | "no error/timeout; not still on the form (silent-fail check)" |

`RESCUE` is **not a stage** — it's a cross-cutting interrupt: any stage can detect "the screen reader choked on this widget" (AXTree node with role but no accessible name / focus-trap) and branch to a rescue sub-flow, then return. This is the most-validated trigger (Aira 62%, foundation §4) and the demo's emotional opener.

### 3.3 "Done" is the hard part — make it explicit

Reddit's 6-month ops-agent lesson: *defining done is "surprisingly difficult, edge cases endless."* So done is **never vibes**: every stage carries a machine-checkable done-predicate **and** a negative-verification list, both evaluated against the freshly re-perceived AXTree in CONFIRM. A stage cannot advance on the model's say-so alone.

---

## 4. The a11y-tree actuator (the Actuator port)

### 4.1 Perception = one merged, numbered tree (the triple-win artifact)

Do **not** feed raw AXTree (flat, verbose, no interaction IDs) or raw DOM (30k–150k tokens). Build the browser-use-style pipeline:

1. **Parallel CDP triple-fetch** (`asyncio.gather`): `DOM.getDocument` (structure) + `Accessibility.getFullAXTree` (role/name/state — what the screen reader sees) + `DOMSnapshot.captureSnapshot` (geometry/paint order).
2. **Simplify** → drop script/style/hidden.
3. **`PaintOrderRemover`** → drop nodes occluded by overlays/modals (kills the "agent clicked the thing behind the cookie banner" bug).
4. **Bbox-containment filter (~99%)** → a button's child icon/text don't get separate indices.
5. **Assign sequential interactive indices → `selector_map`** mapping the LLM-facing number back to the real node.

Target ~2k tokens/viewport (browser-use band), cap at 40k chars.

**Why this artifact is a triple-win for Clarion specifically:**
- (a) It's the model's **observation space**.
- (b) It's the **grounding source** — "read from the accessibility tree, cite the source node" (foundation §9, answer #3). Each `Fact` carries its `source_node_id`.
- (c) It's the **verbalization** — "item 5, the Submit button." The number the model reasons over is the number we can say out loud.

### 4.2 AXTree-primary, vision-fallback (decided)

AXTree-only loses on benchmarks (WebVoyager: 40.1% vs 59.1% Set-of-Marks vision). **We choose AXTree-primary anyway** because reading what the screen reader reads *is the product's trust claim* — a screenshot-grounded readback a blind user can't verify is exactly the thing we refuse to ship. Vision/Computer-Use is the **named, honest fallback** for AXTree-blind widgets (canvas, unlabeled custom controls), in the same posture as the CAPTCHA hard line (foundation §9): we say when we're using it.

### 4.3 Acting

- **Form fill:** native-setter (`Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set` + dispatch `input`) to fill React-controlled inputs the naive `.value=` misses.
- **Navigation/click:** resolve `selector_map[index]` → node → Playwright action.
- **Re-perceive after every act** (CONFIRM reads the *new* tree) → page-diff deltas detect the silently-failed step.

---

## 5. Voice integration (the seam, mechanized)

- **Non-blocking action:** inside the `advance_task` `@function_tool`, launch the graph step with `asyncio.ensure_future(task)` then `await run_ctx.speech_handle.wait_if_not_interrupted([task])`. On barge-in, `speech_handle.interrupted` is true → `task.cancel()` → return None. The graph keeps running in the background even after the agent's sentence ends.
- **Atomic acts:** wrap the actual ACT/⟨PAY⟩ in `run_ctx.disallow_interruptions()` so a stray "um" can't fracture an irreversible step.
- **Speculative retrieval lives in the observer pattern:** a parallel LLM watches the transcript (`conversation_item_added`) and fires the GROUND query on *partial* STT (while the user is still talking), injecting results via `update_chat_ctx` — **never blocks the turn.** This is the foundation §7 effect #1 ("queries fire while you're still talking"), implemented as the observer, not the main loop.
- **Dead-air cover:** background-audio "thinking" sound + `fast-preresponse.py` (timed filler, cancel-on-response) while a web action runs.
- **Barge-in** is LiveKit's semantic turn detection (MultilingualModel), kept live *during* TTS — the foundation §7 effect #4.

---

## 6. Legibility & the demo UI — the same tree, two channels

The numbered AXTree is the legibility substrate. It feeds **two channels**:

1. **Spoken readback (blind user — primary):** the Morae mechanism — the kernel identifies decision points and proactively pauses (`⟨CONSENT⟩`) to give the user the choice, reading only goal-relevant fields in order. Cite the source node; negative-verify; signal uncertainty.
2. **WebRTC participant-attributes (judges/sighted — the on-screen panel):** publish `{stage, step k/n, pending_proposal, consent_state, grounded_facts+sources}` as participant attributes (the `hotel-concierge` pattern). This *is* the foundation §7 six-effect UI:

| Effect (foundation §7) | Source of truth | Wire |
|---|---|---|
| Speculative-retrieval viz | observer fires on partial STT | trace event → panel |
| Live latency meter `Moss 6ms` vs greyed `cold 340ms` | GROUND node timing (§9) | trace event → panel |
| Sources + negative-verification panel | `grounded_facts[].source_node_id` | state → panel |
| Barge-in | LiveKit turn detection | voice plane → panel |
| Consent gate as visible state `AWAITING YOUR YES` | `interrupt()` payload | interrupt → panel |
| Glass-box trace + one-line metric | `trace[]` | state → panel |

**The blind user never needs the panel; the panel never speaks.** Two audiences, one state.

---

## 7. Build order (hour-budgeted for the weekend)

**Reliability is a deliberate ordering, not luck.** Spike the seam before anything pretty.

**Sat AM — THE SEAM SPIKE (de-risk the only unknown):**
- One LiveKit `@function_tool advance_task` → a minimal LangGraph subgraph → merged-AXTree `selector_map` of the demo clone → `PROPOSE` one field → `interrupt()` → speak it → `Command(resume=yes)` → native-setter fill ONE field → CONFIRM re-perceive.
- **Exit criterion:** *speak → plan → perceive → propose → consent → act → confirm* round-trips on a single field, with barge-in cancelling cleanly. If this works, the project works. (D1 seed.)

**Sat midday — Kernel (D1) + ports (D2):**
- Full §2 loop + two-clause policy + two modes; `VoiceTransport`/`Retriever`/`Synthesizer`/`Actuator` behind interfaces. Wire Moss as Retriever; Minimax as Synthesizer; LiveKit as VoiceTransport.

**Sat afternoon — Actuator (§4) + Stages (§3):**
- Full perception pipeline (PaintOrderRemover + bbox filter + selector_map). The hero stage graph with done-predicates + negative verification. The RESCUE cross-cut.
- *Hit the sponsor desks now* (Moss real latency + API, LiveKit, Minimax, Unsiloed) — they judge (prior brief, move #3).

**Sat eve — Demo site (D4) + Legibility/UI (D6):**
- Self-hosted clone with scripted-but-authentic flaws (unlabeled inputs, autopay upsell, layout-shifting confirmation). Participant-attribute panel + the six effects.

**Sun 7:30–11:00 — POLISH ONLY (reserved, no new features):**
- Demo-mode fallback (cached selector_map + scripted proposals so a network blip can't kill the run). Record hero run on the clone + the generality montage (foundation §7). Rehearse the barge-in + consent beats. Write/rehearse the 2-min pitch (D8) around the judge sentence.

---

## 8. Instrumentation — the latency meter (foundation §7 effects #1–2)

The thesis is "retrieval disappears from the budget" — so **measure it on screen.**
- Instrument the `GROUND` node: timestamp query-fire → first-fact. Publish `retrieval_ms` as a trace event.
- Run a **greyed cold-RAG baseline** (local FAISS/Pinecone ~300–400ms, per prior brief) beside the live `Moss <10ms` number. The contrast *is* the proof.
- The observer firing GROUND on partial STT is what makes the live number small — show the query firing *while the waveform is still moving*.

---

## 9. Reliability & recording plan

- **Hero on the self-hosted clone** — scripted flaws, **no real money or credentials** (on-screen disclosure: "Modeled on real sites; sandboxed").
- **Demo-mode fallback** — a flag that serves a cached `selector_map` + scripted proposals so the live run is judge-proof; the autonomous path runs underneath but the demo never depends on a cold network.
- **Montage** — real public sites *up to* any auth/payment wall, or distinct sandboxed clones; never record real credentials or move real money.
- **Recording rules** — captions on; **freeze-frame** tool output, never speed it up; one quantified metric on screen ("found, verified, completed in 90s, unaided").
- **The honest-architecture beat** (prior brief, winner pattern #7): if AXTree-only fails a widget on stage, *say* we fall back to vision — turning the limitation into a credibility win, the Zo/EMPIRE move.

---

## 10. Deliverable mapping (foundation §10)

| D | This doc |
|---|---|
| D1 Kernel | §2 (loop + policy + modes + trace) — seeded by the §7 spike |
| D2 Ports | §1/§2.2 port column + §4 Actuator |
| D3 Persona/narrative | (foundation §0/§3 + judge sentence — not re-derived here) |
| D4 Demo site | §9 |
| D5 Sponsor integrations | §7 Sat-afternoon + §8 (Moss), Synthesizer (Minimax), VoiceTransport (LiveKit) |
| D6 On-screen UI | §6 |
| D7 Demo video | §9 |
| D8 Pitch | §7 Sun |

---

## 11. Open risks / deferred

- **End-to-end loop latency for THIS exact stack is unmeasured** (research gap). The seam spike (§7) is also the first latency probe — measure speak→act→speak then, not in theory. If the AXTree perceive step is too slow per turn, cache the tree and only re-perceive on page-diff signal.
- **AXTree reliability on the *montage* (real) sites** is the fragile layer (WebArena 35.8%). Mitigation: montage clips run *up to* the wall and lean on the clone for the full run; keep the wow in retrieval/verification, not "any website" (foundation out-of-scope).
- **Idempotency of ACT under `interrupt()` re-execution** (§2.3) — the subtle correctness bug; test it explicitly in the spike.
- **Moss's real latency/API** — verify at the sponsor desk Saturday before betting the latency-meter beat on the sub-10ms claim.

---

# Part II — Build Plan & Parallelization (the deck)

> Purpose: make every line item above an **orchestratable unit** — one subagent builds it, the orchestrator verifies it against an explicit acceptance test, status gets tracked. This part is the DAG, the parallel waves, the task register, and the per-task contracts.

## 12. How this runs (orchestration model)

- **Orchestrator (you/main):** owns the DAG, freezes the contracts, spawns one subagent per task, runs each acceptance test, marks status. **Never writes feature code** — only contracts, prompts, and verification.
- **Subagent (per task):** builds exactly one task against the frozen contract section, returns the deliverable **plus its own self-test output**. Hard subtasks (the seam, the actuator filters) can escalate to a Codex rescue pass.
- **Wave ritual:** (1) confirm all deps of the wave are *verified*; (2) spawn the wave's tasks **in one batch** (parallel); (3) collect deliverables; (4) run each acceptance test; (5) mark `done`/`reopen`; (6) only then open the next wave.
- **The keystone rule:** **no Wave-1 task starts until C1 (contracts) is FROZEN.** The spike (S1) is allowed to *revise* the contracts; the freeze happens the moment S1 is green. Parallel work against unfrozen contracts is the #1 way this plan rots.

## 13. Dependency DAG + critical path

```
WAVE 0 (root + free parallels)        ║  WAVE 1 (fan-out, frozen contracts)        ║ W1b        ║ W2     ║ WAVE 3 (Sun polish)
                                      ║                                            ║            ║        ║
C1 Contracts ─┬──────► [FREEZE] ──────╫──┬─ A1 Actuator ──────────────────────┐   ║            ║        ║
C2 Min page ──┤          ▲            ║  ├─ K1 Kernel ───────► ST1 Stages ─────┤   ║            ║        ║
              └─► S1 SPIKE┘ (GATE)    ║  ├─ V1 Voice (LiveKit) ────────────────┼──►║ I1 hero ──►║ POL1 ─►║ REC1 ─► REH1
P1 Persona ──(no deps)────────────────╫──┼─ R1 Moss / R2 Minimax / R3 Ingest ──┤   ║ run        ║ fallbk ║ record  rehearse
Dsite scaffold ─(no deps)─────────────╫──┼─ D2 Demo site (full flaws) ─────────┤   ║            ║        ║
                                      ║  └─ U1 UI panel ───────► L1 Latency ────┘   ║            ║        ║
P1 ───────────────────────────────────╫──────────────► P2 Pitch ────────────────────────────────────────► (feeds REC1/REH1)
```

**Critical path (the serial spine):** `C1 → S1 → K1 → ST1 → I1 → POL1 → REC1 → REH1`. Everything else parallelizes around it. If you're time-boxed, protect this chain; shed scope from R3 / L1 / montage first.

**Max concurrency by wave:** W0 = 4 in flight (C1, C2, P1, Dsite); W1 = up to 8 (A1, K1, V1, R1, R2, R3, U1, D2); W1b = 2 (ST1, L1); W2 = 1 (integration is convergent — one driver); W3 = serial.

## 14. Task register (tracked)

Status legend: ☐ todo · ◐ in-progress · ✅ verified · ⚠ reopened

| ID | Task | Wave | Depends on | Parallel-safe with | Status |
|---|---|---|---|---|---|
| **C1** | Contracts: ports + `ClarionState` + event protocol | 0 | — | C2, P1, Dsite | ☐ |
| **C2** | Minimal target page (one form) | 0 | — | C1, P1, Dsite | ☐ |
| **S1** | **SEAM SPIKE** (one-field round-trip) — GATE | 0 | C1, C2 | — | ☐ |
| **P1** | Persona / narrative kit (D3) | 0 | — | C1, C2, Dsite | ☐ |
| **Dsite** | Demo-site scaffold | 0 | — | C1, C2, P1 | ☐ |
| **A1** | Actuator: perception + act + vision-fallback | 1 | C1 (frozen) | K1, V1, R*, U1, D2 | ☐ |
| **K1** | Kernel: 6-node loop + policy + 2 modes | 1 | C1 (frozen) | A1, V1, R*, U1, D2 | ☐ |
| **V1** | VoiceTransport (LiveKit) + observer + filler | 1 | C1 (frozen) | A1, K1, R*, U1, D2 | ☐ |
| **R1** | Retriever adapter (Moss) | 1 | C1 (frozen) | all W1 | ☐ |
| **R2** | Synthesizer adapter (Minimax) | 1 | C1 (frozen) | all W1 | ☐ |
| **R3** | Ingest (Unsiloed) + Memory adapter | 1 | C1 (frozen) | all W1 | ☐ |
| **U1** | Legibility/UI panel (six effects, mock-fed) | 1 | C1 (frozen) | all W1 | ☐ |
| **D2** | Demo site full (3 scripted flaws + auth wall) | 1 | Dsite | all W1 | ☐ |
| **ST1** | Stage graph (planner + stage nodes + RESCUE) | 1b | K1 | L1 | ☐ |
| **L1** | Latency-meter instrumentation | 1b | K1, R1, U1 | ST1 | ☐ |
| **P2** | Pitch script (D8) | 1 | P1 | all | ☐ |
| **I1** | Integration: full hero run | 2 | A1,K1,ST1,V1,R1,U1,D2 | — | ☐ |
| **POL1** | Demo-mode fallback + reliability | 3 | I1 | — | ☐ |
| **REC1** | Recording (hero + montage) | 3 | I1, POL1, P2 | — | ☐ |
| **REH1** | Rehearsal + final pitch | 3 | REC1, P2 | — | ☐ |

## 15. Task cards (deliverable · acceptance test · subagent seed)

**C1 — Contracts.** *Deliverable:* typed interfaces for all six ports (`VoiceTransport`, `Retriever`, `Synthesizer`, `Actuator`, `Ingest`, `Memory`), the `ClarionState` schema (§2.1), and the plane↔plane event protocol (`advance_task` signature, `interrupt` payload shape, participant-attribute schema). Each port ships a fake/mock impl. *Accept:* import every interface; run a no-op graph that `interrupt()`s and resumes via the mocks; `ClarionState` round-trips through the checkpointer. *Seed:* "Define the §6/§2 contracts only — no real providers. Every port gets an ABC + an in-memory fake. This is the freeze artifact; over-specify the event shapes."

**C2 — Minimal target page.** *Deliverable:* one served page, single text input + submit, at `localhost`. *Accept:* `curl` returns the form; one labeled input present. *Seed:* "Throwaway page for the spike. Nothing fancy — one `<input>`, one submit."

**S1 — Seam spike (GATE).** *Deliverable:* `speak → plan → perceive(merged-AXTree selector_map) → propose one field → interrupt → resume(yes) → native-setter fill → confirm(re-perceive)`, against C2. *Accept:* (a) live round-trip completes; (b) barge-in mid-proposal cancels the in-flight tool cleanly; (c) **resume(yes) firing twice does NOT double-fill** (idempotency, §2.3). *Seed:* "Wire LiveKit `@function_tool` → tiny LangGraph subgraph → CDP triple-fetch selector_map → one-field fill, behind C1 interfaces. Prove the three accept conditions. Escalate to Codex if the LiveKit×LangGraph async seam fights you."

**P1 — Persona/narrative kit.** *Deliverable:* competent-not-helpless rules, tagline, judge sentence, banned-words list ("assistant/helper"). *Accept:* a copy-lint flags any banned word in demo/UI strings. *Seed:* "From foundation §0/§3/§9; produce the voice & banned-words guide + the judge sentence verbatim."

**Dsite — Demo-site scaffold.** *Deliverable:* a buildable site skeleton (routing, a fake utility-account shell) ready to receive flaws. *Accept:* serves a multi-page flow locally. *Seed:* "Scaffold the self-hosted clone shell; leave the scripted flaws to D2."

**A1 — Actuator.** *Deliverable:* the §4 pipeline — parallel CDP triple-fetch → simplify → `PaintOrderRemover` → bbox filter → `selector_map`; native-setter fill; click; re-perceive/diff; vision-fallback stub. *Accept:* on a fixture page **with a modal overlay**, the occluded button is NOT in the selector_map; all real interactables are numbered; <2k tokens/viewport; native-setter fills a React-controlled input; a click yields a non-empty page-diff. *Seed:* "Build §4 behind the `Actuator` interface. The overlay-exclusion + bbox-containment filters are the hard part — unit-test them on fixtures."

**K1 — Kernel.** *Deliverable:* the six §2.2 nodes, two-clause policy, two modes, idempotent ACT, trace emission. *Accept:* VERIFY refuses an ungrounded claim; Normal interrupts every consequential step while Fast interrupts only at the irreversible node; consent re-execution causes no double-act; trace events emitted per node. *Seed:* "Build §2 behind C1. Policy + idempotency are correctness-critical — test them, don't eyeball."

**V1 — Voice.** *Deliverable:* LiveKit `VoiceTransport` impl — STT/turn/barge-in/TTS, `long_running_function` non-blocking, `disallow_interruptions` for atomic acts, observer (speculative retrieval), filler audio. *Accept:* latency log shows the tool overlapping speech (non-blocking); barge-in cancels the in-flight tool; observer fires on a partial transcript without blocking the turn; filler plays on a >1s tool. *Seed:* "Implement §5 behind `VoiceTransport`. Mirror the `long_running_function` + observer examples."

**R1 — Moss / R2 — Minimax / R3 — Ingest+Memory.** *Deliverable:* the respective adapters behind their interfaces. *Accept:* R1 — a known query returns the expected passage **+ a source ref**, p50 ms logged; R2 — a sample utterance synthesizes, TTFB logged, stub-swappable; R3 — one ingested PDF is queryable, a written profile fact reads back. *Seed (each):* "Implement only this one port against C1; keep the fake as the fallback."

**U1 — UI panel.** *Deliverable:* the six foundation §7 effects rendered from participant-attribute + trace **mock** streams. *Accept:* a recorded attribute stream drives all six (latency meter, sources panel, consent-gate state, glass-box trace, speculative-retrieval viz, barge-in indicator). *Seed:* "Build the panel against the C1 attribute schema with mock data — no live agent needed yet."

**D2 — Demo site (full).** *Deliverable:* the three scripted-but-authentic flaws (unlabeled input, autopay upsell, layout-shifting confirmation) + an auth wall; no real money. *Accept:* a screen-reader pass confirms the flaws are *real* (the unlabeled input exposes no accessible name); the autopay upsell is dismissible; the confirmation shifts layout. *Seed:* "Extend Dsite with foundation §7 flaws. The flaws must be real to the AXTree, not cosmetic."

**ST1 — Stage graph.** *Deliverable:* planner emitting the §3.2 six-stage plan; each stage node with a machine-checkable done-predicate + negative-verification list; RESCUE cross-cut; `Command(goto)` transitions. *Accept:* feed a form with one blank required field → `FILL.done == false`; feed an unlabeled-widget fixture → RESCUE triggers; the planner's plan reads aloud as coherent stages. *Seed:* "Build §3 on top of K1. Done-predicates are machine checks, never model say-so."

**L1 — Latency meter.** *Deliverable:* GROUND timing published + greyed cold-RAG baseline beside the live Moss number; query-fire shown during active waveform. *Accept:* on a live turn the panel shows `Moss ms < baseline ms` and the query fires while the waveform is still moving. *Seed:* "Instrument GROUND (§8); wire into U1; add the cold-RAG baseline for contrast."

**P2 — Pitch.** *Deliverable:* 2-min script around the judge sentence + the demo beats. *Accept:* read-aloud ≤ 2:00. *Seed:* "From P1 + §7 demo set; write the 2-min pitch."

**I1 — Integration.** *Deliverable:* the full hero run on D2 — stuck-rescue → verified readback → consented payment behind the fast-mode hard-stop, narrated, UI lit. *Accept:* one clean end-to-end **live** run. *Seed:* "Converge A1+K1+ST1+V1+R1+U1+D2. You are the only driver of this wave — serialize."

**POL1 — Fallback.** *Deliverable:* demo-mode flag serving cached selector_map + scripted proposals. *Accept:* kill the network mid-run → the demo still completes. *Seed:* "Add the §9 demo-mode fallback; the autonomous path runs underneath."

**REC1 — Recording.** *Deliverable:* captioned hero run + 8–12s montage (gov/travel/shopping up to the wall), freeze-framed tool output. *Accept:* video ≤ 3:00, captions on, zero real creds/money. *Seed:* "Record per §9 rules."

**REH1 — Rehearsal.** *Deliverable:* 3 clean run-throughs incl. barge-in + consent beats; pitch timed. *Accept:* timed dry-run ≤ target; barge-in + consent beats land every time. *Seed:* "Rehearse; lock nothing new."

## 16. Verification protocol (the gate ritual)

A task is `✅ verified` only when its **acceptance test passes in the orchestrator's hands** — not when the subagent says "done." Re-run, don't trust the report. On failure → `⚠ reopened` with the failing condition quoted back to a fresh subagent. A wave's downstream tasks stay blocked until *every* dep is `✅`. The seam spike (S1) and integration (I1) get a *live* run, not just unit tests — they're where the seam can lie.

## 17. Scope-shed order (if time runs short)

Drop in this order, protecting the critical path: **R3** (Ingest/Memory → use fakes) → **L1** (latency meter → static number) → **montage** (REC1 hero-only) → **Fast mode** (Normal-only is the safer demo anyway, foundation §5) → **vision fallback** (name it as future work, honestly). Never shed: S1, K1, ST1, A1, V1, I1, the consent gate, or the grounded readback — those *are* the product.

## 18. C1 — the contract spec (FREEZE ARTIFACT)

> Everything in Wave 1 builds against this. Frozen the moment S1 is green. **`contracts/` imports zero provider SDKs** — pure `pydantic` / `abc` / `typing`. Provider imports (LiveKit, Moss, Minimax, Playwright) live only in Wave-1 adapters.

### 18.1 Stack & repo layout (directory ownership = collision-free parallelism)

- **Agent:** Python 3.12+, `langgraph`, `livekit-agents`, `playwright`, `pydantic`.
- **Frontend:** Next.js + `@livekit/components-react` (panel + demo site).

```
agent/clarion/
  contracts/   ports.py · state.py · events.py   ← C1 owns
  fakes/       in-memory impls of every port      ← C1 owns
  kernel/      (K1)   actuator/ (A1)   stages/ (ST1)   adapters/ (R1/R2/R3/V1)
  tests/       contract smoke test                 ← C1 owns
web/spike-target/   one static form                ← C2 owns
web/demo-site/      Next.js clone                   ← Dsite/D2/U1 own
docs/persona.md                                     ← P1 owns
```

### 18.2 Ports (`contracts/ports.py`) — ABCs, kernel sees only these

```python
class VoiceTransport(ABC):
    async def start(self) -> None: ...
    def on_partial(self, cb: Callable[[str], None]) -> None: ...   # observer hook → speculative retrieval
    def on_final(self, cb: Callable[[str], None]) -> None: ...
    def on_barge_in(self, cb: Callable[[], None]) -> None: ...
    async def say(self, text: str, *, interruptible: bool = True) -> "SpeechHandle": ...
    async def play_filler(self, key: str) -> None: ...

class Retriever(ABC):
    async def query(self, q: str, *, k: int = 5) -> list["Fact"]: ...     # ranked facts + source refs

class Synthesizer(ABC):
    async def synthesize(self, text: str) -> AsyncIterator[bytes]: ...    # text → audio stream

class Actuator(ABC):
    async def perceive(self) -> "SelectorMap": ...                       # merged numbered AXTree
    async def act(self, action: "Action") -> "Observation": ...          # click/fill/navigate → obs
    async def diff(self, before: "SelectorMap", after: "SelectorMap") -> "PageDiff": ...

class Ingest(ABC):
    async def ingest(self, doc: bytes | str) -> list["Passage"]: ...

class Memory(ABC):
    async def write(self, fact: "Fact") -> None: ...
    async def read_profile(self, user_id: str) -> "Profile": ...

# SpeechHandle protocol: .interrupted: bool ; await .wait_if_not_interrupted([task])
```

### 18.3 State (`contracts/state.py`)

```python
class Stage(BaseModel):
    id: str; goal: str
    tools: list[str]                      # tool subset for this stage
    done_predicate: str                   # name of a registered checker fn
    negative_checks: list[str]

class Fact(BaseModel):
    value: str
    source_node_id: str | None            # AXTree node → grounding; None ⇒ ungrounded ⇒ may NOT be spoken
    polarity: Literal["present","absent"] = "present"   # supports negative verification
    verified: bool = False
    retrieved_at: float

class Action(BaseModel):
    kind: Literal["click","fill","navigate","read"]
    index: int | None = None              # into SelectorMap
    value: str | None = None
    irreversible: bool = False            # gates Fast mode

class Proposal(BaseModel):
    id: str
    utterance: str                        # the spoken readback
    action: Action | None
    irreversible: bool

class SelectorMap(BaseModel):
    nodes: dict[int, "AxNode"]            # index → {role,name,state,bbox,node_id}
    token_estimate: int

class ClarionState(TypedDict):            # the durable, checkpointed goal-state (§2.1)
    goal: str; mode: Literal["normal","fast"]
    plan: list[Stage]; stage_idx: int; step: tuple[int,int]
    page_index: SelectorMap
    grounded_facts: list[Fact]
    pending_proposal: Proposal | None
    consent_log: list["Consent"]
    trace: list["TraceEvent"]
```

### 18.4 Event protocol (`contracts/events.py`) — the plane↔plane / plane↔frontend wire

```python
# Voice plane → task plane (documented signature; the @function_tool wrapper lives in V1, not here)
#   advance_task(user_intent: str | None) -> str
#   MUST be non-blocking: launch the graph step, await speech_handle.wait_if_not_interrupted([task]).
class AdvanceTaskRequest(BaseModel): user_intent: str | None = None

# Task plane → voice plane: the payload LangGraph interrupt() surfaces at ⟨CONSENT⟩
class ConsentRequest(BaseModel):
    proposal_id: str
    utterance: str                        # speak this readback
    irreversible: bool
    options: list[str] = ["yes","no","edit"]

# Resume: Command(resume=ConsentDecision(...))
class ConsentDecision(BaseModel):
    decision: Literal["approve","reject","edit","respond"]
    value: str | None = None

# Task plane → frontend: published as a LiveKit participant attribute (JSON), drives the six §6 effects
class PanelState(BaseModel):
    stage: str; step: tuple[int,int]
    proposal: str | None
    consent_state: Literal["idle","awaiting_yes","approved","rejected"]
    grounded_facts: list[Fact]
    retrieval_ms: float | None; baseline_ms: float | None
    trace_tail: list["TraceEvent"]
```

### 18.5 Freeze rule
Any Wave-1 subagent that finds this spec **unimplementable or ambiguous** stops and reports the gap to the orchestrator — it does **not** silently diverge. Contract changes flow through the orchestrator and re-freeze for everyone.

### 18.6 C1 build outcome + resolved decisions (2026-05-31)
C1 is implemented and verified (`agent/`, 8/8 smoke tests pass; contracts/fakes import **zero** provider SDKs). Versions locked: **langgraph 1.2.2** (`InMemorySaver` canonical, `MemorySaver` legacy alias; `interrupt`/`Command` live in `langgraph.types`), **pydantic 2.13.4**, **Next.js 16.2.2 + React 19** (Turbopack default; `@livekit/components-react` recorded for U1). Decisions resolved by the orchestrator (so the freeze is real):
- **`step: tuple[int,int]` is kept as declared.** The JsonPlus checkpointer round-trips it as a `list`; **consumers coerce on read** (`tuple(state["step"])`). Do not relax the annotation.
- **`Consent` ≡ `ConsentDecision`** shape (`proposal_id, decision, value, at`) — accepted; it doubles as the §2.3 idempotency guard (ACT checks the consent_log before side-effecting).
- **`Observation / PageDiff / Passage / Profile`** were under-specified in §18.3; C1 gave them minimal shapes. **A1 (actuator) and R3 (ingest/memory) must confirm these field sets** against their real adapters before their own Wave-1 freeze.
- **Checkpointer durability (K1 action item):** langgraph 1.2.2 deserializes the custom contract models today but logs a future-removal warning. K1's checkpointer **must allowlist the contract module** (serde `allowed_msgpack_modules = [["clarion.contracts.state","SelectorMap"], …]`) to keep the §2.1 durability claim valid. Contracts stay pure; this is an adapter-side setting.
- **FROZEN (2026-05-31, S1 green).** The seam spike passed the gate (round-trip + barge-in cancel + resume-twice idempotency) against the real LiveKit↔LangGraph↔CDP seam and forced **zero** contract changes. It positively validated two §18.6 items: the checkpointer `allowed_msgpack_modules` allowlist round-trips the contract models with no warnings, and `Consent`-as-idempotency-guard holds (the ACT once-flag prevents double-fill). Wave 1 may now build against these contracts.
  - **Seam discovery (informs V1):** the LiveKit `google.beta.GeminiTTS` plugin **cannot** consume an `AQ.*` Vertex-Express key (its `vertexai=True` branch nulls the api_key and demands a GCP project/ADC). TTS therefore goes through a `google-genai` SDK Express-mode `Synthesizer` (`genai.Client(vertexai=True, api_key="AQ.*")`) — proof the ports design pays off. V1 inherits `agent/spike/tts_vertex.py`.
  - **External blocker (account-side, NOT code):** the Gemini keys resolve to GCP project `956065465952`, which returns `403 — Lightning dunning decision is deny` (billing suspension) on every call. The voice plane is fully wired and dies only here. The live LLM/TTS end-to-end runs once billing is restored or a fresh key on a healthy project is supplied. **Do not swap models** (standing rule).

### 18.7 Contract amendment (re-freeze 2026-05-31, Wave 1 / K1)
The only contract change Wave 1 forced. `ClarionState.trace` and `consent_log` now carry `Annotated[list[...], operator.add]` reducers. **Why:** LangGraph channels are last-value-wins, so a node returning `{"trace": [ev]}` would *overwrite* the audit log — silently breaking the §2.3 idempotency guard, which reads prior ACT/approve markers out of `trace`/`consent_log` on an `interrupt()` resume. **Rule for all nodes (K1, ST1, integration):** return ONLY new entries to these two channels; the reducer concatenates. `grounded_facts` deliberately stays last-value-wins (nodes manage it explicitly — accumulation would stale/dup). All other §18.3 fields unchanged.

---

## Part III — Build status & handoff (2026-05-31)

> The plan in Parts I–II is **built, verified, and committed** on branch `docs/execution-plan` (14 feature/chore commits). Every task was verified by the orchestrator re-running its acceptance test — never the subagent's word. This section is the run-it-all reference + the event-day checklist.

### 19. What's done (component → file → proof)

| Component | Lives in | Verified |
|---|---|---|
| **Contracts** (6 ports, `ClarionState`, events) — FROZEN, zero provider SDKs | `agent/clarion/contracts/` | imports + checkpointer round-trip |
| Fakes (every port) | `agent/clarion/fakes/` | ABC conformance |
| **Kernel** — GROUND→VERIFY→PROPOSE→⟨CONSENT⟩→ACT→CONFIRM, 2-clause policy, 2 modes, idempotent ACT | `agent/clarion/kernel/` | policy refuses ungrounded; Normal/Fast gating; resume-twice acts once |
| **Actuator** — merged-AXTree perception (parallel CDP triple-fetch + PaintOrderRemover + bbox filter → `selector_map`), native-setter act, diff; vision fallback named-stub | `agent/clarion/actuator/` | occluded button excluded; <2k tok/viewport; native-setter persists |
| **Stages** — planner (6-stage hero plan), per-stage nodes + done-predicates + negative-checks, `Command(goto)`, RESCUE cross-cut | `agent/clarion/stages/` | blank field → FILL.done False; unlabeled widget → RESCUE fires |
| **Voice** (VoiceTransport) — LiveKit + Deepgram STT + Gemini LLM + Vertex/AI-Studio Gemini TTS + Silero VAD + turn detection; observer hook; non-blocking advance | `agent/clarion/adapters/` | ABC conformance; real `SpeechHandle` cancel path; live LLM+TTS |
| **Instrument** — `TimedRetriever`, cold-RAG baseline, `to_panel_state` | `agent/clarion/instrument/` | measures real ms; PanelState mapping |
| **Retrieval** (R-Moss) — `MossRetriever` + `GeminiMossIngest` + `MossMemory`; Gemini custom embeddings (routes around Moss's broken embed host / 503 cloud-query) | `agent/clarion/retrieval/` | LIVE ingest→query, correct ranking, ~0–1ms in-memory search |
| **App** — `HeroRuntime`, `voice_entry` (LiveKit worker), `hero_harness`, `demo_mode` (CachedActuator), `kb_beat` (live Moss beat) | `agent/clarion/app/` | LIVE hero GREEN; OFFLINE demo-mode GREEN |
| **Demo site** (scripted a11y flaws) | `web/demo-site/` | unlabeled input name='' via AXTree; upsell dismissible; layout-shift |
| **Panel** (six legibility effects) | `web/panel/` | serves; mock + `?live=1` participant-attributes |
| **Spike target** | `web/spike-target/` | S1 gate page |
| **Persona kit** + copy-linter | `docs/persona.md`, `scripts/copy_lint.py` | banned-word lint fails/passes |

### 20. Run it all

```bash
# --- deterministic regression gate (no network) ---
cd agent && pip install -e ".[test]" && python -m pytest clarion        # 82 passed, 3 deselected
python -m pytest clarion -m live                                          # 3 live Moss tests (needs creds)

# --- the demo site (the hero target) ---
cd web/demo-site && npm install && npm run dev -- --port 8770             # login pw: demo

# --- the FULL hero run (live: real Playwright + Moss + Gemini) ---
cd agent && pip install -e ".[spike]" && pip install -e ".[retrieval]"
.venv/bin/playwright install chromium
DEMO_SITE_URL=http://localhost:8770/ .venv/bin/python -m clarion.app.hero_harness

# --- the JUDGE-PROOF offline run (site can be DOWN) ---
CLARION_DEMO_MODE=1 .venv/bin/python -m clarion.app.hero_harness

# --- the live voice worker (a human speaks) ---
.venv/bin/python -m clarion.app.voice_entry console     # or `dev` to join a LiveKit room

# --- the legibility panel (live participant attributes) ---
cd web/panel && npm install && npm run dev               # open with ?live=1
```
All secrets in `agent/.env` (gitignored; template `agent/.env.example`).

### 21. Credential / provider state (event-day truth)
- **LiveKit** — live (`agent/.env`).
- **Gemini LLM + TTS** — LIVE via **AI Studio** (`GOOGLE_API_KEY`, `gemini-3.5-flash` + `gemini-3.1-flash-tts-preview`/Kore). The Vertex-Express `AQ.*` key is billing-blocked (kept behind `TTS_MODE=vertex` for the event if the AI-Studio ~100/day TTS cap bites). **Never swap models** (standing rule).
- **Moss** — live retrieval works; its built-in embed host (TLS-broken) + cloud-query (503) are bypassed by Gemini `gemini-embedding-001` custom embeddings. The `clarion-kb` index is built + persistent (reused, not re-ingested).
- **Minimax** — Gemini TTS stands in (swap seam = the `Synthesizer` ABC; comment in `tts_vertex.py`).
- **Deepgram** — live STT.

### 22. Event-day checklist (what's left)
- [ ] **Live spoken run** — `voice_entry console`, a human speaks the hero flow (mechanism proven; only the mic is human-in-loop).
- [ ] **REC1** — record the hero run + the generality montage (foundation §7 rules: captions on, freeze tool output, no real money/creds).
- [ ] **REH1** — rehearse the 2-min pitch (`docs/persona.md` judge sentence) + the barge-in/consent beats.
- [ ] **Sponsor desks** — confirm Moss's real latency claim, grab credits, ask each judge "what wins?" (research move #3).
- [ ] Pre-warm the Moss index + fire the embed on partial-STT so the on-stage number is the **in-memory ~3ms** (wall-clock embed RPC ~2.7s must overlap speech).
- [ ] **Fallback drill** — rehearse `CLARION_DEMO_MODE=1` so a venue-network failure is a non-event.

### 23. Known gaps (honest)
- **2 demo-site polish items:** the pay form marks no field `required` (FILL's `no_required_field_blank` is vacuously safe) and the `$` balance sits in a non-interactive `<strong>` (REVIEW's interactive-tree scan returns False; it cross-checks the live form value instead). The KB-level negative-verification ("no late fee") IS real via Moss (I2). Both are ~10-min `web/demo-site` tightenings, not logic bugs.
- **TTS daily cap** — AI Studio ~100 TTS req/day; restore the Vertex project or a fresh key for a heavy event.
- **Live tests** — 3 Moss integration tests depend on an intermittently-available sponsor service; gated behind `-m live` so the regression gate stays deterministic.
- **git** — loose objects from an early `node_modules` mis-stage; `git prune` clears (home-rooted repo, left to your discretion).
