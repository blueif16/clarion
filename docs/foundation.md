# Clarion — Product Foundation (FINALIZED)

_generated 2026-05-31 • supersedes `docs/tandem-foundation.md` • status: scope-locked for hackathon, company-thesis flagged as open_

**Name:** **Clarion** _(LOCKED 2026-05-31. Considered and dropped: Helm, Candor, Sonar, Cue.)_
**Tagline:** _Clarion — you're in command._
**One-liner:** A voice co-pilot that lets blind and low-vision people finish private, high-stakes online tasks themselves — it finds the right thing, reads back exactly what's there (and says when it *can't* find something instead of guessing), and keeps the human in command at every consequential step.

> ⚠️ Naming note: avoid "assistant / helper" language anywhere in copy. The blind community resents the helpless framing. The product gives **agency**, not assistance.

---

## 0. How we got here (the reasoning trail — recorded so we don't relitigate)

1. **Started broad:** research on the YC Conversational AI Hackathon (LiveKit/Moss/Minimax) surfaced "voice is cheap, retrieval is the bottleneck" and the winning pattern of speculative retrieval + per-step consent. (See `research/conversational-ai-hackathon-2026-05-30.md`.)
2. **Two-product idea:** one core, two hackathons — a Google/Elastic cut and a YC cut. The shared invariant ("no fact without a source, no action without a yes") was identified as the durable IP.
3. **Adversarial office-hours trial:** stress-tested the blind-accessibility framing. It surfaced **two real load-bearing risks** cheaply: (a) the accessibility-overlay *backlash* (the category has antibodies), and (b) **payments** as the scariest possible target.
4. **Honesty pass (multi-source research):** ran Reddit + Exa to get ground truth instead of guessing. Result: the *design* is literature-validated, but the *use-case was pointed at the wrong target* (autonomous payment).
5. **The re-aim (this doc):** keep the domain, audience, design, and two modes. **Move the hero** from "an agent that pays your bill for you" to "a task-aware co-pilot that verifies and keeps you in command," with payment demoted to a single consented demo beat behind a hard-stop.

**The lesson, stated plainly:** the design was right; it was aimed at the most dangerous, least-validated behavior. Re-aimed, not rebuilt.

---

## 1. The invariant (unchanged — this is the core)

> ## No fact without a source. No action without a yes.

- **Epistemic clause (grounding):** never *speak* a fact it didn't just retrieve, including negatives ("there is **no** late fee on this page"). If it can't ground a claim, it says so. It never guesses.
- **Agentic clause (consent):** never *commit an irreversible side-effect* without an explicit per-step "yes."

Everything else (voice provider, retrieval backend, web-action engine, even the domain) is a swappable port. This contract is the kernel.

---

## 2. Who it's for + the core insight (the Support-track bridge)

**Target user:** blind / low-vision people who are *expert* assistive-tech users (NVDA/JAWS/VoiceOver) and fiercely value independence. They are not helpless; the websites are broken.

**The insight that frames the whole product (validated):** an inaccessible self-service flow **forces the user into a human channel.** Evidence:
- **76.5%** of blind banking users had to ask another person for help [First Monday n=162].
- **60%** fall back to *phone* banking because the app/site failed [ADA Southeast].
- **~2/3** of e-commerce interactions get abandoned [McKinsey].
- Customer-service reps "frequently didn't have clear escalation paths for accessibility," so issues went unresolved [McKinsey].

**So the product's value (and the Support-track pitch):** every failed self-service task = a forced support escalation (phone agent, branch, sighted helper). Clarion is **self-service containment / escalation deflection for the disabled-customer segment** — a real customer-service KPI. For a company deploying it: *"make self-service actually complete for the 1-in-40, deflect the escalations you're eating today, and stop forcing blind customers to phone in or give up."* That is a customer-service product, cleanly.

---

## 3. What it does — the task-aware co-pilot (the re-aimed hero)

**"Task-aware" is the pillar. It means goal-state tracking.** The whole differentiation lives here:

- **Generic describer (Be My AI, today):** reactive, scene-level. You ask "what's here?" and it narrates the whole screen. It doesn't know your goal. The literature's exact complaint: *"Be My AI loves general descriptions — it doesn't know what to focus on."*
- **Task-aware co-pilot (Clarion):** holds the goal ("pay my electric bill"), so it ignores page noise, reads only goal-relevant fields *in the order you need them*, tracks progress ("card done, 2 fields left"), knows what *done* looks like, detects a silently-failed step, and verifies the **goal-relevant facts** (amount, payee, due date) instead of describing everything.

That gap — *describes the screen* vs *drives the task and knows where you are in it* — is the product. It is genuinely unserved: describers aren't goal-oriented; general web agents (Operator, ChatGPT Atlas — scored 1/10 for screen-reader access) are inaccessible and break on real sites.

**Re-aim summary:** lead with verified, in-command task completion. Payment is *one consented capability*, not the reason the product exists. The trust story leads with **agency + verifiability**, not autonomous money movement, and not "it's local" (that lever is unvalidated — see §8).

---

## 4. Main target workflows (ranked, evidence-tagged)

**Tier 1 — Validated, high-frequency, in scope (build here):**

| Workflow | Evidence | Task-aware behavior |
|---|---|---|
| **Complete an inaccessible form / checkout** | Aira: **65%** use it for inaccessible online forms; WebAIM ranks complex forms top-problematic; **54.3%** blocked from bill pay | Reads only goal-relevant fields in order, fills with per-step confirm, negative-verifies "no required field left blank," catches validation errors the screen reader never announced |
| **"Rescue me, I'm stuck" mid-task** | Aira: **62%** troubleshoot when AT fails | The strongest co-pilot *trigger*: detect the widget the screen reader choked on; explain/operate it with consent. The exact moment they'd otherwise call support |
| **Login / identity verification** | UsableNet case: unlabeled buttons → account lockout | Walk the auth flow, read the right control, confirm before submit. Usually the *first* wall before any task |
| **CAPTCHA wall** | Aira: **66%** (#1); ADA: **70%** trouble | Highest demand, but **do NOT auto-solve** — assist the audio-CAPTCHA flow or hand off. Name it as the wall we don't fake (see §9) |

**Tier 2 — Validated as high-barrier (completion matters, help likely wanted):**
- Banking / bill pay — 80%+ bank weekly; 54% blocked [First Monday] → **high-stakes: this is the fast-mode hard-stop**
- Online shopping / checkout — 86% barriers; 2/3 abandonment [AFB, McKinsey]
- Travel booking — **91–94%** barriers (highest measured) [AFB]
- Food ordering 88%, job applications 90%, government/benefits portals (gov sites cost 68 min per barrier) [AFB]

**Tier 3 — Reasoned assumption (did NOT surface as discrete demand; high-likelihood, flagged honestly):**
- Dispute a charge / return an item / file a claim; track an order; manage/cancel a subscription; book or reschedule an appointment; update account settings.
- These are classic support tasks and probably frequent, but were **not** directly evidenced (Reddit "top" sort buried mundane asks). Treat as plausible, not proven. A `sort=new` r/Blind pass would test them.

**Demo hero (DECIDED):** form/checkout completion **triggered by a "stuck" moment** (sits on the most-validated demand: forms 65% + rescue 62%, low-stakes, best shows task-awareness), **climaxing in a consented bill payment as the fast-mode hard-stop** (keeps the emotional payoff while the defensible substance is the rescue + verification). Full demo set in §7.

---

## 5. The two modes (Claude-Code-style permission model)

Default-safe, with a power mode that still respects the irreversible line.

- **Normal mode (DEFAULT):** human-in-the-loop, per-step confirmation. This is the validated design (Morae beat Operator on exactly this). Every consequential step waits for an explicit "yes."
- **Fast mode (opt-in, demo + power user):** the agent runs ahead through the *reversible, low-stakes* steps (navigate, read, fill) **but hard-stops at any irreversible/financial step.** Framed as: *"the agent earns autonomy on the boring steps, never on the irreversible one."*

**Why the hard-stop matters:** a mode that auto-pays for a blind user *is* the "done to them, not by them" pattern the community hates, and money is the top of the trust scale. The hard-stop is both more honest and a better story than blanket YOLO.

**Know-your-room (demo):** at a general AI hackathon (YC/LiveKit/Moss), showing fast mode plays well. In front of accessibility-savvy judges, lead with Normal mode and make the *verification* the wow. Same product, different opening shot.

**Modes are an autonomy slider, not a trust solution.** They govern *who pulls the trigger*. They do **not** solve whether a blind user can believe the agent's readback (see §9, real worry #1).

---

## 6. Architecture (kernel + ports — shared across both hackathon cuts)

The invariant lives in a fixed loop. **The kernel imports zero provider SDKs.** It only talks to ports; per event you rewire the adapters.

```
intent ─▶ GROUND ─▶ VERIFY ─▶ PROPOSE ─▶ ⟨CONSENT GATE⟩ ─▶ ACT ─▶ CONFIRM + REMEMBER
       (Retriever)          (assert only        (Normal: every step;     (Actuator)
                             grounded facts,      Fast: only at the
                             incl. negatives)     irreversible step)
```

| Port | YC Support cut | Google/Elastic cut | Kernel sees only |
|---|---|---|---|
| `VoiceTransport` | **LiveKit** (barge-in, turn detection) | Gemini Live | partial+final transcripts, barge-in events |
| `Retriever` | **Moss** (sub-10ms) | Elastic MCP | `query → ranked facts[] + source refs` |
| `Synthesizer` | **Minimax** | Gemini audio | `text → audio` |
| `Actuator` | Playwright **accessibility-tree** + Computer-Use fallback | same | `action → observation` |
| `Ingest` | **Unsiloed** (parse company docs → KB) | site crawl | `doc → indexed passages` |
| `Memory` | Moss/Atlas write-back | Elastic/Atlas | `write(fact)` / `read(profile)` |
| **Kernel** (never swaps) | — | — | the loop + the two-clause policy + the two modes + the glass-box trace |

**Common (~80%):** kernel loop, policy, two modes, a11y-tree perception, glass-box trace, persona/narrative/demo craft.
**Per-event (~20%):** which adapters are wired, and which beat is the hero. **Rule:** build behind the three swap interfaces (`VoiceTransport`, `Retriever`, `Synthesizer`) early; never satisfy both events in one binary at one event.

---

## 7. The demo (YC Support track)

**Track choice: Support** — "service bots that instantly pull docs and user history." The grounding clause *is* that; accessibility is the differentiation (everyone else ships a generic FAQ voice bot). Co-Pilot fails on "display" (blind user); Lead Gen is the wrong shape.

**Hero scenario:** a blind user completes a stuck task on a **self-hosted clone** of a real utility/account site built with authentic accessibility flaws (unlabeled inputs, an autopay upsell, a layout-shifting confirmation). On-screen disclosure: *"Modeled on real sites; sandboxed to protect real financial data."* Reliable, honest, privacy-safe.

**Demo set (FINALIZED — one primary + a generality montage):** demo *one* task live (or as the main recorded run); prove generality with a short screen-recording montage of the same Clarion co-pilot on the *most-troublesome validated* tasks, so it never looks hardcoded to one site (the winning "generality montage" pattern).
- **Primary (live / main run):** utility **bill-pay** on the self-hosted clone — stuck-rescue → verified readback → consented payment behind the fast-mode hard-stop. (Validated bullseye + emotional payoff + fully controlled = reliable.)
- **Montage (~8–12s, screen-recorded), the same agent on the next-worst validated tasks, by barrier severity + relatability:**
  1. **Government / benefits portal form** — gov sites cost **68 min per barrier**, 20% end in abandonment [AFB]; highest dignity stakes.
  2. **Travel booking** — the single **highest-barrier** task measured, **91–94%** [AFB]; universally relatable.
  3. **Online shopping checkout** — **86%** barriers, ~2/3 abandonment [AFB/McKinsey]; the everyday one.
- **The "stuck rescue" beat** (screen reader chokes on a misbehaving widget; Clarion detects + operates it with consent) — the most-validated trigger (Aira **62%**). Lives inside the hero, and can also open the montage.

**Reliability / privacy rules for the recordings:** hero on the self-hosted clone (scripted flaws, no real money or credentials). Montage clips use real public sites *up to* any auth/payment wall, or sandboxed clones of visibly-distinct archetypes — never record real credentials or move real money. Captions on; freeze-frame tool output, never speed it up.

**Effects that impress (ranked by "impresses AND proves the thesis"):**
1. **Speculative-retrieval visualization** — queries fire *while the user is still talking*. Most on-thesis effect.
2. **Live latency meter** — `Moss retrieval: 6ms` vs greyed `cold RAG: 340ms`. Retrieval disappears from the budget, on screen.
3. **Live sources + negative-verification panel** — every spoken fact cited; "no late fee [verified: not present]." The RAG-of-Fire trust beat.
4. **Barge-in** — interrupt mid-sentence, instant stop (LiveKit turn detection).
5. **The consent gate as a visible state** — `AWAITING YOUR YES` at the autopay upsell and at submit. The agency differentiator.
6. **Glass-box trace** + a one-line metric ("found, verified, completed in 90s, unaided") + the human close: *"I did it myself."*

**Judge sentence:** *"A voice support co-pilot with a hard rule — it never says anything it didn't retrieve and never does anything you didn't approve — which is why retrieval has to be instant. Watch a blind customer finish a task that would normally force a phone call, in command the whole way."*

---

## 8. Supporting evidence (the receipts — cite these, with the corrections baked in)

**Design validation (the design is literature-backed):**
- **Morae** (UIST'25, Peng/Li/Bigham/Pavel; DOI 10.1145/3746059.3747797, arXiv 2508.21456) — proactively pausing UI agents for blind users beat OpenAI Operator: **Awareness-of-Actions 6.2 vs 4.9; Results 6.4 vs 4.6.** ✅ verified, numbers accurate. This is essentially a peer-reviewed version of our thesis.
- **CHI 2026 WoZ study** (blind users + Operator) — human-in-the-loop confirmatory double-check before payment/shipping is **required, not optional.** Validates Normal mode.
- **Task Mode** (arXiv 2507.14769) — source-referencing + negative verification improves trust. ⚠️ **Cite μ=4.08** for the source-grounding trust score, **not 4.41** (4.41 was a different simplicity/control measure). Do not repeat 4.41 on stage.

**Task-frequency (grounds "task-aware" in real workflows):**
- **Aira 2024 Explorer Survey** — 66% inaccessible elements/CAPTCHA, 65% inaccessible forms, 62% AT-failure rescue. (Paid demand = revealed demand.)
- **Aira call-log study** (PMC, n=10,022 calls) — reading 35%, navigation 33%, home mgmt 16%.
- **AFB Barriers to Digital Inclusion** — travel 91–94%, job apps 90%, food 88%, shopping 86%; diary: gov sites 68 min/barrier, 20% of barriers end in abandonment.
- **WebAIM Screen Reader Survey #10** (n=1,539) ✅ — CAPTCHA #1; 58% prefer mobile app over web for banking/shopping; 85.9% want more accessible sites; web got better 34.6% / worse 18.6%.
- **First Monday banking survey** (n=162) — 80%+ bank weekly, 63.6% blocked, 76.5% had to ask for help, 54.3% blocked from bill pay.
- **ADA Southeast** — 85% had site problems, 70% CAPTCHA trouble, 60% fall back to phone banking.
- **McKinsey** — ~2/3 e-commerce abandonment; CS lacks accessibility escalation paths.
- **Esposito CA study** (IJHCI 2026, DOI 10.1080/10447318.2026.2659951) ✅ — conversational agent vs screen reader, 30 BLV participants.

**Trust / risk research:**
- **ASSETS 2024** — blind users *over-trust* confident AI, can't catch hallucinations, verify more for financial/medical.
- **arXiv 2604.00187** — trust is calibrated by stakes; agent irreversibility breaks the "call a human to verify" fallback.
- **~22% incorrect-answer rate** (arXiv 2602.13469, GPT-4o diary study) — the payment-grade reliability gap.
- **Be My Eyes "Groups"** — users route financial docs to *named, trusted humans*, not AI. Trust currency = human accountability, not technical privacy.

**Overlay backlash (the category antibody):**
- **Overlay Fact Sheet** (800+ signatories incl. WCAG/ARIA editors), **NFB resolution**, **$1M FTC fine vs accessiBe.** Targets *vendor-installed, business-side, deceptively-marketed widgets that override the user's AT without consent.* A user-owned, opt-in, per-step-consent co-pilot is the structural opposite (firsthand reviewers praised Gemini's "Take Over Task" control).

---

## 9. Worries — recorded so we answer them, not dodge them

### 9a. Likely judge / YC questions (and our answers)
- **"Isn't this just an overlay like accessiBe?"** → No. User-owned, opt-in, per-step consent, never installed business-side, never overrides the screen reader. The structural opposite of what the community burned down.
- **"Why would a blind power user trust your AI over their own NVDA skills?"** → We don't replace the screen reader. We rescue the moments it fails — the exact thing 62% already *pay Aira* for. Augment, never seize.
- **"How does a blind user verify the agent didn't misread the amount?"** → Read from the accessibility tree (not a screenshot), cite the source node, negative-verify, cross-check the amount against the known balance/expected payee, then per-step consent + hard-stop on anything irreversible. Honest: this *reduces*, doesn't *eliminate* (see 9b).
- **"What about payments and liability?"** → Demoted to a consented beat behind the Normal-mode hard-stop. We do not autonomously move money.
- **"Won't Apple/Google just do this?"** → Real risk (see 9b). Our edge is blind-specific UX + verification discipline + cross-site reach; for the hackathon it's a demo, for a company the moat is still open.
- **"Is the AI central?"** → Yes: goal-state planning, page perception, verification, and turn-taking are all model-driven.

### 9b. Real unsolved issues (the honest list)
1. **Non-visual verification of a visual medium (the core).** A blind user can't independently confirm the agent perceived the page correctly; per-step confirm relies on a spoken readback they can't cross-check. Mitigated (a11y tree, citations, negative verification, cross-checks) but **reduce-not-eliminate, forever.**
2. **Reliability for irreversible actions** (~22% error in current models). We decline autonomous irreversible actions on purpose (hence Normal mode + hard-stop) — which means "autonomous high-stakes completion" stays unsolved by us.
3. **CAPTCHA** — #1 wall, not legitimately auto-solvable. We assist/hand-off; don't build the pitch on beating it.
4. **Demand for delegation is unvalidated.** What's validated is demand for *description + task-rescue with the user acting*; nobody in the research said they'd pay for an agent to autonomously do their banking. Our re-aim leans on the validated behavior.
5. **Distribution + platform risk (company-level).** Apple/Google ship free OS-level accessible agents and own the platform.
6. **Liability** — a third party touching banking credentials inherits PCI/fraud/regulatory exposure incumbents absorb more easily.
7. **Advocacy positioning** — the community's north star is "fix the source." A tool that lets businesses skip remediation invites criticism. Framing ("a user's tool, not a compliance band-aid") manages it; never fully gone.
8. **"Local/privacy" is an unvalidated trust lever** — no Reddit signal; trust currency is human accountability. Don't bet the trust story on it.

### 9c. Hackathon vs company (the honest split)
- **As a hackathon project: strong.** Emotional, Morae-backed design, real whitespace, demos beautifully. Build the re-aimed version.
- **As a company: not yet, not this framing.** Delegation demand unproven + distribution/platform risk + payment liability. Not "give up" — "the hackathon is the right container, and the company thesis needs a different wedge before betting years."

---

## 10. Scope (finalized) + deliverables

**In scope (hackathon):**
- Voice co-pilot, Normal mode default + Fast mode with hard-stop.
- Task-aware completion of forms/checkout + "stuck rescue" trigger + login/identity flows.
- Verification-first behavior (grounded readback, negative verification, uncertainty signaling).
- Self-hosted demo site with authentic, scripted accessibility flaws.
- One consented payment beat (sandboxed, fast-mode hard-stop).
- On-screen demo UI: speculative-retrieval viz, latency meter, sources/verification panel, consent-gate state, glass-box trace.

**Out of scope (named explicitly):**
- Autonomous payment / autonomous irreversible actions.
- CAPTCHA auto-solving.
- Business-side deployment that overrides the user's AT.
- Betting the trust narrative on "local/private."
- General-purpose "any website" navigation as the hero (it's the fragile layer; keep the wow in retrieval/verification).

**Deliverables:**
- **D0** This foundation doc ✅
- **D1** Kernel: control loop + grounding/consent policy + two modes + trace, behind the port interfaces
- **D2** The three swap adapters defined (`VoiceTransport`, `Retriever`, `Synthesizer`)
- **D3** Persona + narrative kit (the "competent, not helpless" rules; tagline; judge sentence)
- **D4** Self-hosted demo site with scripted flaws
- **D5** YC sponsor integrations wired (LiveKit, Moss, Unsiloed, Minimax, TrueFoundry/AWS)
- **D6** On-screen demo UI (the six effects)
- **D7** ~3-min demo video (recorded hero run + generality montage, captioned)
- **D8** 2-min pitch script around the judge sentence

---

## 11. Open questions / next
- ✅ Name locked: **Clarion**.
- ✅ Demo set locked: bill-pay hero + government/travel/shopping generality montage (§7).
- `sort=new` r/Blind pass to test Tier-3 task frequency (optional).
- The next document is **execution** (build order, instrumentation for the latency meter, the a11y-tree actuator, reliability/recording plan) — deliberately out of scope here.

---

### Appendix — research artifacts
- `research/conversational-ai-hackathon-2026-05-30.md` — hackathon thesis, speculative retrieval, latency budgets, past-winner patterns.
- This conversation's research passes — VoC citation audit (Morae/Task Mode/WebAIM/Esposito verified; 4.08 correction), demand/trust reality, task-frequency data (Aira/AFB/WebAIM/banking surveys).
