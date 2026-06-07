# Pinocchio — Demo Script & Run-of-Show

_The single source for the final presentation. Product name in the pitch: **Pinocchio** (codebase/foundation still say Clarion). Decisions locked: **real government sites, no clone, no real money — drive to the consent hard-stop and stop.** One task only — bill pay. Banned words apply (no "assistant/helper/assist") — `scripts/copy_lint.py`._

---

## 0. The decision (why real sites, not the clone)

- The clone reads as a toy. **"We did this live, on a real government website"** is the credible claim.
- You **cannot complete a real payment** (real money, real credentials). So the demo runs **navigation → grounded readback → form-fill → the consent hard-stop**, and **STOPS at the irreversible step.** It never moves real money.
- This is not a limitation — it's the product working correctly (persona: *"the hard-stop is a feature, not a failure"*). Pinocchio voices it: *"I won't submit this without your yes. In a demo we stop here — Pinocchio never moves real money for you. On your own account, your yes completes it."*
- Use obviously-fake field values (e.g. test card `4111 1111 1111 1111`); never submit.

---

## 1. Key copy (say these; all banned-word-clean)

**One-liner (the name + the whole positioning in one breath):**
> "Screen readers *read* the web to you. AI browsers *do* tasks — but they make things up and act without asking. **Pinocchio does the task — and it can't lie, and it can't act without your yes.**"

**What it is (if asked):** a **voice co-pilot** that lets blind people finish high-stakes web tasks themselves — *not a browser* (don't compete with Chrome/Atlas), a co-pilot that rides on the browser.

**The problem (right after the one-liner — the terrible situation, then cut straight to the demo):**
> "Three out of four blind people have had to hand a password to someone else — or just give up — to finish a private task online: pay a bill, fill a form, send a payment. The highest-stakes tasks, the ones with money on the line, are exactly the ones today's tools fail. AI browsers do the task but make things up — you can't trust a word. Screen readers are honest, but they only *read* — they leave you to do everything yourself, and they don't care whether you ever reach your goal. So the only option left is to give up your independence and ask another person."
> _Then cut to the demo:_ "Watch how easily it gets done — alone, in command."

(Scale to cite: **76.5%** have had to ask another person for help · **54.3%** blocked from bill pay · **60%** fall back to phone banking.)

**The honesty close (the climax — after the demo has already shown it):**
> "Every fact you heard traced to the page. Every action waited for your yes. The model proposes — the code disposes. That's not a prompt; it's enforced in code. 193 tests, real .gov sites."

**The human payoff (end on this):** *"I did it myself."* → tagline **"Pinocchio — you're in command."**

---

## 2. Run-of-show (the demo is the MAJORITY — never invert this)

_For a ~3-min slot. The setup is bounded; every extra second goes to the DEMO, never to more talking._

| Beat | Time | Content |
|---|---|---|
| **Hook + one-liner** | ~12s | The name + the one-liner (§1) |
| **The problem** | ~25s | The scale + why today's tools fail + forced to ask another person (§1). Show the problem slide, then *stop talking and show it.* |
| **THE DEMO** | **~95–120s** | Live, on a real .gov site — bill pay, proves everything (§3) |
| **Honesty close** | ~20s | The guarantee + "I did it myself" (§1) |

**The problem slide (show it while you say the problem):**

| AI browsers (Atlas/Comet) | Screen readers (JAWS/NVDA) |
|---|---|
| ✅ Get the task done | ❌ Just read — goal-blind |
| ❌ Confidently make things up | ✅ Honest (it's just the page) |
| ❌ Act without asking | ❌ You still do all the work |

→ **Pinocchio: does the task · can't lie · can't act without your yes.**

---

## 3. The demo flow (live, on a real government site — bill pay only)

_Spoken lines are kernel-authored (the airtight verbatim path). Atlas appears only as a short recorded clip — never run a competitor live._

| # | Beat | ~Time | Spoken (kernel lines) | On screen | Live/rec |
|---|---|---|---|---|---|
| 1 | **Open + speculative retrieval** | 10s | User: "Pay my electric bill." Pinocchio: "Paying your electric bill. I'll read only what's needed — say stop anytime." | Queries fire mid-sentence; `Moss 6ms` vs greyed `cold RAG 340ms` | live |
| 2 | **Grounded readback + source-node highlight** | 20s | "Amount due: [reads live] — from the amount field." "Due date: [reads live] — from the due-date label." | Each fact → its source node highlights (node-driven, **not bbox**) | live |
| 3 | **Verifiable negative — head-to-head** ⭐ | 18s | User: "Is there a late fee?" Pinocchio: "No late fee on this page — I checked the fee section, the field is absent." Atlas (rec): "No late fees — you're all set!" | Pinocchio: "verified absent — nothing to point at." Atlas: nothing to show | L live · R **rec** |
| 4 | **Form-fill Q&A pair** | 22s | "Card-number field — I read 4111… — confirm to fill, or correct me." User: "Confirm." "Filled. Two fields left." | The field + its **paired label** highlight together (PairedFact) | live |
| 5 | **Action-trace feed** (runs throughout) | — | — | Each step pops a toast: read → *reversible ✓*; submit → *IRREVERSIBLE 🔒* (persists) | live |
| 6 | **Consent hard-stop = the climax** 🔒 | 25s | "Submit will send [amount] to [payee]. This is irreversible. Say yes to proceed, or stop. — In a demo we stop here; Pinocchio never moves real money for you." | Big `AWAITING YOUR YES`; the irreversible toast holds | live |
| 7 | **Close** | 15s | The honesty line + "I did it myself." | `Every fact sourced · 0 ungrounded words spoken` + tagline | live |

**Production rules:** cut the ~2s think-gaps (freeze on the panel — never speed up); captions on; the panel readable; only the head-to-head second is recorded.

---

## 4. Real government site targets (ranked, guest-pay verified)

| Site | Task | Guest (no login)? | AXTree-friendly? | Role |
|---|---|---|---|---|
| **Pay.gov** `pay.gov/public/home` → "Pay My Debt" / VA copay | Federal debt/copay | ✅ | ✅ semantic, 508-mandated; fillable guest forms | **Primary "do the task"** — you can actually fill a guest form and reach a real submit/consent gate |
| **SMUD** `myaccount.smud.org/unauthenticatedpayment` | **Electricity bill** | ✅ | ✅ cleanest (2 labeled fields, own domain) | **Relatable readback + negative** ("pay my electric bill") — field validation blocks deep fill |
| **Seattle City Light / Denver Water** | Electricity / water bill | ✅ (account + ZIP) | ✅ city .gov | Clean backups |

**Rules:** drive to the gate, **never complete**; **never enter real credentials**; pick sites whose payment stays **on their own domain** (above) — **avoid utilities that hand card entry to a third-party iframe** (Austin Energy, CPS → Paymentus).

**Pick one flow:** run the whole thing on **Pay.gov** for a single coherent fillable flow; or read-back + negative on **SMUD** (the relatable "electric bill" line) → form-fill + hard-stop on **Pay.gov**.

---

## 5. The most troublesome website types — NEVER touch live

Each is either **pixel-only** (AXTree empty → nothing to cite) or **nondeterministic** (the demo-killer):
- **Canvas apps** — Google Docs/Sheets, Figma/FigJam, Canva, Miro, maps. Zero DOM.
- **Late-hydrating SPAs** — fail *intermittently* (the Panera empty-shell). Worst property for a recording.
- **Closed shadow DOM** — Salesforce Lightning.
- **Cross-origin iframe payment frames** — Stripe/PayPal/**Paymentus**.
- **CAPTCHA + selfie/liveness 2FA** (ID.me, Login.gov doc-upload) — inaccessible to blind users; **stop before any identity step**.
- **Anti-bot walls** (Cloudflare Turnstile, PerimeterX, Akamai). NOTE: the **extension transport runs in the user's own session**, so anti-bot barely touches the live product demo.

**The sweet spot (hard-looking but winnable):** **server-rendered government / public-utility forms** on Section-508 / WCAG-mandated sites. They *look* hard (screen readers mis-surface controls on long forms) but are *winnable* — mandated ARIA → AXTree-rich → **you ground every fact on a real source node while a vision agent guesses coordinates.** That contrast is the head-to-head.

---

## 6. Cite these numbers correctly

- Safe: **76.5%** had to ask another person · **54.3%** blocked from bill pay · **60%** phone-banking fallback · **65%** blocked on inaccessible forms (Aira) · unlabeled forms = WebAIM #1.
- **CORRECTION:** "91–94% travel" = barrier-*encounter* rate (AFB), **not** a task-failure rate.
- **CORRECTION:** "68 min per barrier on gov sites" is **unverified** — drop it or source it.

---

## 7. Why honesty matters MORE for this audience (the one line that wins)

A sighted user catches a hallucination with a glance — that glance is a free safety net. A blind user has none. So an agent that *can't lie* isn't a nice-to-have here — **it restores the verification that blindness removes.** That's why this is accessibility, not a truthfulness gimmick. (MacLeod CHI 2017: blind users over-trust AI even when output is nonsensical; a wrong read "could not be corrected even with human assistance.")

---

## Sources
- Real-site targets + hardest-site map: `research/demo-target-sites-2026-06-07.md`.
- Demo-craft + prevention beats: `docs/research/prevention-demos-showcase-2026-06-06.md`.
- Positioning + barrier data: `docs/foundation.md` §2/§4/§8; persona register: `docs/persona.md`.
