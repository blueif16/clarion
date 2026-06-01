<div align="center">

# Clarion

### A voice co-pilot that lets blind and low-vision people finish private, high-stakes web tasks *themselves*.

**It finds the thing, reads back exactly what's on the page — and says when it _can't_ find something instead of guessing — and keeps the human in command at every consequential step.**

_Clarion — you're in command._

`LiveKit` · `Moss` · `Deepgram` · `Gemini` · `Minimax` · `LangGraph` · `Playwright/CDP` · `Next.js 16`
Built for the **YC Conversational AI Hackathon** (June 6–7 2026) · Track: **Support**

</div>

---

## The one idea

Voice models are now cheap and fast. **Retrieval is the bottleneck — and trust is the product.** For a sighted user, a wrong word on screen is a shrug. For a blind user who can't cross-check the page, a confident hallucination during a bill payment is a disaster. So Clarion is built on one non-negotiable contract:

> ## No fact without a source. No action without a yes.

- **Epistemic (grounding):** Clarion never *speaks* a fact it didn't just retrieve — including negatives ("there is **no** late fee on this page"). If it can't ground a claim, it says so. It never guesses.
- **Agentic (consent):** Clarion never *commits an irreversible action* (pay, submit, enroll) without an explicit, per-step "yes."

Everything below — the architecture, the demo, the reason this wins — is downstream of that one invariant.

---

## 1. System architecture

Two planes and an actuator, wired by **events, not nested loops**. The kernel imports **zero provider SDKs** — every vendor lives behind a port, so the whole stack is swappable.

```mermaid
flowchart TB
    human(["🗣️  Human — speaks / hears"])

    subgraph VOICE["🎙️ Voice Plane · LiveKit — owns the &lt;800ms turn budget"]
        direction LR
        STT["Deepgram<br/>STT"] --> TURN["turn-detect<br/>· barge-in"] --> TTS["Minimax / Gemini<br/>TTS · filler"]
    end

    subgraph TASK["🧠 Task Plane · LangGraph kernel — 6-node loop · idempotent ACT"]
        direction LR
        G["GROUND"] --> V["VERIFY<br/>only grounded facts<br/>+ negatives"] --> P["PROPOSE"] --> C{{"CONSENT GATE<br/>interrupt() = 'yes?'"}} --> A["ACT"] --> CF["CONFIRM<br/>+ remember"]
        CF -.->|"next sub-goal"| G
    end

    subgraph ACTU["🖱️ Actuator · Playwright / CDP"]
        direction LR
        AX["numbered<br/>Accessibility Tree"] --> SM["selector_map"] --> AC["act"] --> RP["re-perceive<br/>· diff"]
        RP -.->|"loop"| AX
    end

    MOSS[("📚 Moss · ~3ms<br/>fact + source_node_id")]

    human ==>|"speech"| STT
    TTS ==>|"audio"| human
    TURN -->|"advance_task() · non-blocking"| G
    C ==>|"surfaces 'yes?'"| TTS
    MOSS -->|"grounded facts"| G
    P -->|"selector_map → act"| SM
    RP -->|"observation"| V

    classDef gate fill:#fde68a,stroke:#d97706,stroke-width:2px,color:#1c1917;
    classDef store fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e;
    class C gate
    class MOSS store
```

<details>
<summary><i>Plain-text fallback (if your viewer doesn't render Mermaid)</i></summary>

```
HUMAN ⇄ Voice Plane [LiveKit: Deepgram STT · turn-detect · barge-in · TTS]   (<800ms turn budget)
                │ advance_task()                        ▲ CONSENT surfaces "yes?" via TTS
                ▼  (non-blocking)                       │
Task Plane [LangGraph]:  GROUND → VERIFY → PROPOSE → ⟨CONSENT⟩ → ACT → CONFIRM ──┐ next sub-goal
   ▲ grounded facts          ▲ observation             │ selector_map → act      │
   Moss (~3ms) ──────────────┘                         ▼                         │
                              Actuator [Playwright/CDP]: AXTree → selector_map → act → re-perceive ⟲
```
</details>

**The six-node loop** (`kernel/graph.py`) is the heart. `GROUND` retrieves; `VERIFY` asserts only grounded facts (and confirmed absences); `PROPOSE` states the intended action and the facts behind it; the **`CONSENT` gate** (LangGraph `interrupt()`) blocks on an irreversible step until the human says yes; `ACT` is idempotent (re-executes from the top on resume, checks the consent-log once-flag before side-effecting); `CONFIRM` reads back the result and writes it to memory.

**Ports (the swap seams) — `contracts/ports.py`, FROZEN:**

| Port | Live adapter (event-day) | Responsibility |
|---|---|---|
| `VoiceTransport` | **LiveKit** (+ Deepgram STT) | audio in/out, turn detection, barge-in |
| `Retriever` | **Moss** (Gemini `gemini-embedding-001` custom embeddings) | `query → grounded facts[] + source_node_id`, sub-10ms in-memory |
| `Synthesizer` | **Minimax** (Gemini TTS stands in behind the same ABC) | `text → audio` |
| `Actuator` | **Playwright/CDP** | merged numbered AXTree → act → re-perceive |
| `Ingest` | site / doc → KB | parse + index pages and policy docs |
| `Memory` | checkpointer + write-back | durable goal-state, per-site quirks, confirmations |

**Why the accessibility tree, not screenshots:** the AXTree is the same structured surface a screen reader consumes — robust on messy real sites (Shadow DOM, unlabeled fields, mid-flow layout shifts) where vision-based agents break. It's also how Clarion *grounds* its readback: every spoken fact carries a `source_node_id`. A `Fact` with `source_node_id = None` is ungrounded and is **never spoken** — the invariant, enforced in code.

**Two modes** (`kernel/policy.py`, a 2-clause policy):
- **Normal (default):** human-in-the-loop, per-step confirmation. The validated design.
- **Fast (opt-in):** runs ahead through reversible steps (navigate, read, fill) but **hard-stops at any irreversible/financial step.** "Earns autonomy on the boring steps, never on the irreversible one."

**Stack (locked):** Python 3.12+, `langgraph 1.2.2` (`interrupt`/`Command`, `InMemorySaver`), `pydantic 2.13.4`, `playwright`, `livekit-agents` · Next.js 16.2.2 + React 19 (Turbopack), `@livekit/components-react`. Providers split by extra: `.[test]` (no network), `.[spike]` (LiveKit+Playwright+genai), `.[retrieval]` (Moss+genai).

**Repo layout** (directory ownership = collision-free):
```
agent/clarion/contracts/   ports · state · events            ← FROZEN; pure pydantic/abc
            /kernel/        graph (6-node loop) · policy (2 modes)
            /actuator/      merged-AXTree perception + act + diff
            /stages/        planner + per-stage nodes + RESCUE cross-cut
            /adapters/      voice_livekit · tts (real providers live here)
            /retrieval/     Moss + Gemini-embedding stack
            /instrument/    latency meter · cold-RAG baseline · to_panel_state
            /app/           runtime · hero_harness · voice_entry · demo_mode
web/demo-site/   (hero target)  ·  web/panel/ (six demo effects)  ·  web/spike-target/
docs/foundation.md (why) · docs/execution.md (build) · docs/persona.md
```

---

## 2. Why this wins

### Why there's a real gap

We checked the field, hard. The market splits into two halves and **nobody owns the middle**:

- **Describers** (Be My AI, Seeing AI, Aira) are loved but **stateless and not goal-oriented** — the literature's exact complaint: *"Be My AI loves general descriptions, it doesn't know what to focus on."* It tells you about the screen; it doesn't drive the task.
- **General web agents** (Operator, ChatGPT Atlas, Gemini Auto Browse) are goal-oriented but **inaccessible by default** (Atlas scored **1/10** for screen-reader access), **break on messy real sites**, and **strip agency** — they auto-pick options and leave the user unsure anything happened.

Clarion is the unserved intersection: **task-aware + voice-first + blind-specific + per-step verified.** No shipping product sits there.

### Why the demand is real (and where we were honest with ourselves)

The validated, *paid* demand is for exactly Clarion's core — digital-task **rescue**, not autonomous delegation:

- **Aira 2024 Explorer Survey:** 66% use it for inaccessible elements/CAPTCHA · **65% for inaccessible online forms** · **62% to troubleshoot when their screen reader fails.** People pay humans for this today.
- **WebAIM Screen Reader Survey #10** (n=1,539): CAPTCHA is the #1 wall; 58% route to a mobile app to escape broken web.
- **Banking** (First Monday, n=162): 80%+ bank weekly; **54.3% are blocked from bill pay; 76.5% had to ask another person for help.**
- **The framing insight:** every failed self-service flow **forces a support escalation** (phone agent, branch, sighted helper) — ~2/3 of e-commerce gets abandoned (McKinsey). Clarion is **escalation deflection for the disabled-customer segment**, a real customer-service KPI. That's the Support track, cleanly.

### Why the design is right (it's peer-reviewed)

This isn't a hunch. **Morae** (UIST '25) ran the study: proactively pausing a UI agent for blind users — Clarion's exact design — beat OpenAI Operator on **Awareness-of-Actions (6.2 vs 4.9)** and **Results (6.4 vs 4.6).** A 2026 CHI Wizard-of-Oz study found a human-in-the-loop double-check before payment is *required, not optional*. ASSETS '24 showed blind users over-trust confident AI and verify more for money — which is **why** grounding + consent is the whole product, not a feature.

### Why it's a good demo

The hackathon thesis is "retrieval is the bottleneck." Clarion's invariant puts retrieval **on the critical path of every utterance** — the agent literally cannot speak without grounding first — so the thesis isn't a slide, it's the load-bearing wall. The demo makes that **visible**:

1. **Speculative retrieval** — queries fire *while the user is still talking* (on partial STT). The thesis, on screen.
2. **Live latency meter** — `Moss: ~3ms` next to a greyed `cold RAG: ~340ms`. Retrieval disappears from the budget.
3. **Sources + negative-verification panel** — every spoken fact cited; *"no late fee — verified: not present."*
4. **Barge-in** — interrupt mid-sentence, instant stop.
5. **The consent gate as a visible state** — `AWAITING YOUR YES` at the autopay upsell and at submit.
6. **Glass-box trace** — every step and the "why" behind it.

**Demo set:** one primary live run — utility **bill-pay** on a self-hosted clone with authentic accessibility flaws (stuck-rescue → verified readback → consented payment behind the hard-stop) — plus a **generality montage** of the same agent on the worst real tasks: government/benefits portals (68 min per barrier), travel booking (91–94% barriers), shopping checkout (86%). It never looks hardcoded. The human close: *"I did it myself."*

---

## 3. Readiness, the hard questions, and our task focus

### How we're ready

- **Deterministic regression gate:** `82 passed, 3 deselected`, fully offline (`.[test]` pulls no network).
- **Providers live (event-day):** LiveKit · Deepgram STT · Gemini LLM+TTS (AI Studio) · **Moss retrieval live**, `clarion-kb` index built and persistent. Minimax swaps in at the `Synthesizer` seam.
- **Judge-proof offline path:** `CLARION_DEMO_MODE=1` replays the hero run with no network, so a venue Wi-Fi failure can't kill the demo. Reliability is an engineering choice, not luck.
- **Latency engineered, not hoped:** Moss is pre-warmed and the embed fires on partial-STT so the on-stage retrieval number is the in-memory **~3ms**, inside LiveKit's **<800ms** turn budget.

```bash
# deterministic gate (no network)
cd agent && pip install -e ".[test]" && python -m pytest clarion          # 82 passed, 3 deselected

# the hero, fully live (Playwright + Moss + Gemini)
cd web/demo-site && npm install && npm run dev -- --port 8770             # hero target (login pw: demo)
cd agent && pip install -e ".[spike]" && pip install -e ".[retrieval]"
.venv/bin/playwright install chromium
DEMO_SITE_URL=http://localhost:8770/ .venv/bin/python -m clarion.app.hero_harness

# judge-proof offline replay  ·  live voice worker  ·  the six-effect panel
CLARION_DEMO_MODE=1 .venv/bin/python -m clarion.app.hero_harness
.venv/bin/python -m clarion.app.voice_entry console
cd web/panel && npm run dev
```

### The hard questions (and our answers)

We pressure-tested Clarion in an adversarial founder review. The answers are in the design, not in spin.

- **"Isn't this just an accessibility overlay like accessiBe?"** — No, it's the structural opposite. Overlays are *vendor-installed, business-side,* and *override the user's screen reader without consent* (which is why the community got a $1M FTC fine levied and 800+ experts to sign against them). Clarion is **user-owned, opt-in, per-step consent, never installed on the business's site, never overrides the AT.**
- **"Why would a blind power user trust your AI over their own NVDA skills?"** — We don't replace the screen reader. We rescue the moment it *fails* — the exact thing 62% already pay Aira for. Augment, never seize.
- **"How can a blind user verify the agent didn't misread the amount?"** — Read from the accessibility tree (not a screenshot), cite the `source_node_id`, negative-verify, cross-check the amount against the known balance, then per-step consent + a hard-stop on anything irreversible. Honest limit: this *reduces*, it doesn't *eliminate* — non-visual verification of a visual medium is a forever-hard problem, which is exactly why the consent gate exists.
- **"What about payments and liability?"** — Payment is a *consented* beat behind the Normal-mode hard-stop. Clarion does not autonomously move money. The demo runs on a sandboxed clone — no real credentials, no real funds.
- **"Won't Apple/Google absorb this?"** — Real risk, and we say so. Our edge is blind-specific UX + verification discipline + cross-site reach. For the hackathon it's a demo that works today; the company moat is an honest open question (see `docs/foundation.md §9c`).
- **"Is the AI actually central?"** — Yes: goal-state planning, page perception, grounded verification, and turn-taking are all model-driven. Strip the model and there's no product.

### How we define ourselves: **task focus**

The word that separates Clarion from everything else is **task-aware** — it tracks **goal-state**. A describer narrates a screen; Clarion holds the goal ("pay my electric bill"), ignores page noise, reads only the goal-relevant fields *in order*, tracks progress, knows what *done* looks like, detects a silently-failed step, and verifies the facts that matter (amount, payee, due date) instead of describing everything. The validated workflows we focus on, in priority order:

1. **Complete an inaccessible form / checkout** (Aira 65%; bill-pay 54% blocked)
2. **"Rescue me, I'm stuck"** when the screen reader chokes on a widget (Aira 62% — our strongest trigger)
3. **Login / identity verification** (the first wall; documented account-lockout failures)
4. **CAPTCHA** — the #1 wall, which we *assist/hand-off,* never pretend to auto-solve

We are deliberately **not** an autonomous-payment agent, not a CAPTCHA-defeater, and not a business-side compliance band-aid. Saying no to those is what keeps the trust.

---

## What's honestly unsolved

Judges respect candor more than a clean story, so: non-visual verification is reduce-not-eliminate; current models miss too often for *autonomous* irreversible actions (hence the hard-stop); CAPTCHA stays a wall; demand for *delegation* (vs. assisted self-action) is unproven; and at company scale, distribution against the platforms is an open question. Clarion is scoped precisely around what the evidence supports — and explicit about the rest.

---

## Docs & evidence

- **`docs/foundation.md`** — the full why: reasoning trail, evidence with citations (and corrections), two modes, worries, scope.
- **`docs/execution.md`** — the build spec.
- **`docs/persona.md`** — Maya, and the "competent, not helpless" voice rules.
- **`research/`** — the hackathon thesis + winner patterns, and the execution-architecture brief.

> **Invariant, one more time, because it's the whole thing:** _No fact without a source. No action without a yes._
