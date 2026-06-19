# Pinocchio — Demo Script & Run-of-Show

_The single source for the final presentation. Product name in the pitch: **Pinocchio** (codebase/foundation still say Clarion). Decisions locked: **real public-service sites, no clone, no real money — drive to the consent hard-stop and stop.** One task only — **book a campground reservation on Recreation.gov** (Point Reyes). Banned words apply (no "assistant/helper/assist") — `scripts/copy_lint.py`._

---

## 0. The decision (why real sites, not the clone)

- The clone reads as a toy. **"We did this live, on a real federal booking site"** is the credible claim.
- You **cannot complete a real booking** (real money, real account login). So the demo runs **navigation → grounded readback of the availability grid → pick-a-date → the consent hard-stop**, and **STOPS at the irreversible book/pay step** (which is behind login anyway). It never books or charges.
- This is not a limitation — it's the product working correctly (persona: *"the hard-stop is a feature, not a failure"*). Pinocchio voices it: *"I won't book this without your yes. In a demo we stop here — Pinocchio never books or pays for you. On your own account, your yes completes it."*
- Use obviously-fake values where a field is filled; never submit a real reservation.

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

## 3. The demo flow (live, on Recreation.gov — book a campsite, stop at the gate)

_Spoken lines are kernel-authored (the airtight verbatim path). Atlas appears only as a short recorded clip — never run a competitor live._

| # | Beat | ~Time | Spoken (kernel lines) | On screen | Live/rec |
|---|---|---|---|---|---|
| 1 | **Open + jump-to-destination** | 12s | User: "Make a reservation to Point Reyes." Pinocchio: "Point Reyes isn't on this page — Recreation.gov has the Point Reyes National Seashore Campground. Taking you there — say stop anytime." | SITE MAP semantic match → campground URL; `Moss 6ms` vs greyed `cold RAG 340ms` | live |
| 2 | **Grounded grid summary + source-node highlight** | 20s | "Availability for Point Reyes: 7 sites, dates June 7 through 16 — from the calendar header." "Which date and which site?" | The communicator summarizes the GRID AXES; each axis (date columnheader, site rowheader) is a real source node, highlighted (node-driven, **not bbox**) | live |
| 3 | **Verifiable negative — head-to-head** ⭐ | 18s | User: "Is the 10th open for Boat A?" Pinocchio: "No — June 10, Site Boat A shows reserved; that's the cell I checked." Atlas (rec): "Yes, looks available!" | Pinocchio: the exact cell highlights, state read from its node. Atlas: guesses | L live · R **rec** |
| 4 | **Pick-by-voice Q&A pair** | 22s | "June 12, Site Boat A reads available — confirm to select, or correct me." User: "Confirm." "Selected. Date and site set." | The one date×site cell highlights; re-perceive reads the effect | live |
| 5 | **Action-trace feed** (runs throughout) | — | — | Each step pops a toast: read/select → *reversible ✓*; book → *IRREVERSIBLE 🔒* (persists) | live |
| 6 | **Consent hard-stop = the climax** 🔒 | 25s | "Booking Site Boat A for June 12 will reserve and charge your card — irreversible, and it needs your login. In a demo we stop here; Pinocchio never books or pays for you." | Big `AWAITING YOUR YES`; the irreversible toast holds | live |
| 7 | **Close** | 15s | The honesty line + "I did it myself." | `Every fact sourced · 0 ungrounded words spoken` + tagline | live |

**Production rules:** cut the ~2s think-gaps (freeze on the panel — never speed up); captions on; the panel readable; only the head-to-head second is recorded.

**Mic discipline (the 06-11 lesson):** the open mic transcribed ~30 minutes of ambient room chatter as user turns — a stray "Yeah" even triggered a consent. Use a close-talk mic, MUTE whenever not addressing Pinocchio, and rehearse in the real room. `clarion-up.sh` pre-sets `CLARION_STT_KEYTERMS` (Deepgram keyterm boosting) so "Point Reyes" is heard as Point Reyes, not "Ponteries" — override it if the demo task changes.

---

## 3★. The chosen live flow — "Make a reservation to Point Reyes" on Recreation.gov (page-by-page, VALIDATED 2026-06-07)

_This is the concrete instantiation of §3 — it refines the §4 site pick. **Why a campground reservation, not bill-pay:** the ask ("make a reservation to Point Reyes") is natural and high-stakes, the availability table is a **HUGE grounded data-picker** (every date×site is a real AX node — the honesty story tells itself), and the hard-stop line is clean ("about to book and charge for [site] on [date]"). The whole chain is **one origin** (`www.recreation.gov`), server-rendered, AXTree-rich. Everything below was driven on the **real** site (headless Playwright + Moss round-trip + the live MiniMax planner) — the ✅/⚠️/🔨 status tags are empirical, not aspirational._

**The validated chain (all `www.recreation.gov`, same-origin):**
- **A — Home** `https://www.recreation.gov/` — search-driven homepage. Point Reyes is **not reliably a homepage anchor** (it's a featured-carousel link today, which rotates) → the SITE MAP (or the site's own search) is how we reach the campground, not a brittle link-crawl.
- **B — Campground** `https://www.recreation.gov/camping/campgrounds/233359` — title **"Point Reyes National Seashore Campground."** The availability grid: **dates (columns) × sites (rows)**, each cell a clickable `button` with a self-describing name (`'Jun 10, 2026 - Site BOAT A, 1-6 people is available'`).
- **C — Order/Login** `…/camping/reservations/orderdetails?…` — booking/payment is **behind auth** (a fresh session redirects to `Login - Recreation.gov`) → the demo STOPS at the consent/login wall by design.

**The two-case resolution (VALIDATED 2026-06-07 on the LIVE MiniMax planner — `probes/recreation_planner_verify.py`):**
- **Present (indexed):** "make a reservation to Point Reyes" → SITE MAP top hit = `…/campgrounds/233359` → the planner's **step 0 navigates straight to the campground page.**
- **Absent (not indexed):** "reserve a campsite at Zion" → the planner **announces** *"no exact match for Zion in the known site map — using the site's search to look it up"* and **falls back to Recreation.gov's own Search** — never a confident wrong page. This is the de-hardcoded honesty beat at the **nav layer**: the Moss score is a normalized RANK (every camping goal tops out near 1.0, even an un-indexed one — `probes/recreation_multidoc_calib.py`), so a numeric threshold can't reject the miss — **the LLM does** (the SITE-MAP candidates are framed as may-not-contain-the-target; `_with_site_map`, `stages/graph.py`).

**Setup BEFORE the demo — PRE-WARM the SITE MAP (don't gamble on live timing):**
- `clarion-up.sh` now **defaults to the Recreation.gov home** and starts the worker with **`CLARION_SITE_KNOWLEDGE=1`** (step 3), so the planner consults the SITE MAP at PLAN time. Pre-warm the campgrounds you'll demo — **seed at a SEARCH url, not the homepage** (the homepage anchor-crawl does not reliably reach a campground):
  ```
  CLARION_CRAWL_MAX_PAGES=1 CLARION_CRAWL_MAX_DEPTH=0 MOSS_EMBED_MODEL=moss-minilm \
    .venv/bin/python -m clarion.app.site_indexer \
    "https://www.recreation.gov/camping/campgrounds/233359" \
    "I want to make a reservation to Point Reyes"
  ```
  **VALIDATED 2026-06-07:** ingesting the campground page → a warm query for "make a reservation to Point Reyes" returns `…/campgrounds/233359` as the **top hit** (`SiteKnowledge.context_facts(home, goal)` — the planner's exact call — surfaces it). A COLD index (nothing ingested) returns 0 hits → the planner falls back to search. Both cases drove the live planner correctly.

**Page-by-page (what the user says · what Pinocchio does · the functionality it proves · status):**

| # | Page | User says | Pinocchio (spoken + action) | Functionality proven | Status |
|---|---|---|---|---|---|
| 1 | **A (home)** | "Make a reservation to Point Reyes." | "Point Reyes isn't on this page — Recreation.gov has the *Point Reyes National Seashore Campground*. Taking you there." | SITE MAP semantic match (NOT lexical) → campground URL **from the home page** | ✅ validated (live planner step 0 = navigate to 233359) |
| 1b | **A (absent variant)** | "Reserve a campsite at Zion." | "No exact match for Zion in what I know — searching the site for it." | NAV-layer honesty: absent destination → search fallback, never a wrong page | ✅ validated (live planner step 0 = announce + search) |
| 2 | **A→B redirect** | "Yes." | `navigate` to the campground URL → **your real tab moves**. | Cross-page navigate classifies `unknown` → routes through the **CONSENT interrupt** (`kernel/irreversibility.py`); reversible-but-still-asked = the "no redirect without a yes" beat | ✅ built (gate) · ⚠️ rehearse the line |
| 3 | **B (the grid) — grounded summary** ⭐ | — | "Point Reyes availability: dates June 7 through 16 across the top, sites like Boat A and Boat B down the side. Which date and site?" | The communicator summarizes the GRID AXES (columnheaders = dates, rowheaders = sites) — **not** 599 cells; every axis is a real AX source node | ✅ **grid-axis summary WIRED** (`summarize_ax_tree` surfaces `columnheader`/`rowheader` roles into the readout — structural, not lexical; `pipeline._grid_axis_phrase`) + 599 nodes validated |
| 4 | **B — verifiable negative** ⭐ | "Is the 10th open for Boat A?" | "No — June 10, Site Boat A shows reserved; that's the cell I checked." (or "Yes — it's available.") | Reads the EXACT cell's state from its node name, incl. the honest negative — a vision agent guesses | ✅ node data validated · 🔨 wire to voice |
| 5 | **B — pick the date** | "Book June 12, Boat A." | "June 12, Site Boat A reads available — selecting it." → click that exact node → re-perceive → read the effect | Voice → semantic resolve to the ONE node → click → confirm | ✅ **cell click validated** (`probes/recreation_cell_click_probe.py`: clicking the exact date×site cell selects it → **"Add to Cart"** appears, +4 nodes) · 🔨 the voice-resolve line |
| 6 | **B→C hard-stop 🔒** | (we stop) | "Booking Site Boat A for June 12 will reserve and charge your card — irreversible, and it needs your login. In a demo we stop here — Pinocchio never books or pays for you." | Irreversible book/pay → **CONSENT hard-stop** (the climax); payment is behind auth → a clean place to stop | ✅ hard-stop built · ⚠️ payment page needs the logged-in tab |

**The grid, as the AXTree surfaces it (all observed live — `probes/recreation_axtree_probe.py`):** the perception captured **599 interactive nodes** — `515` date×site `button`s (each `'Jun N, 2026 - Site X … is available/reserved'`), `10` `columnheader` dates (`'Sunday, June 7, 2026'`…), site `rowheader`s, and two `spinbutton` month pickers (Start/End date). The visible window is **two weeks** (10 date columns); "one month" steps the spinbuttons. No truncation — there is no node cap.

> ⚠️ **The payment page is behind auth — by design the wall we stop at.** A fresh headless browser has no session, so `…/orderdetails?…` redirects to `Login - Recreation.gov`. Perception still works (it cleanly surfaced the login form's required `Email`/`Password` textboxes with `settable: True`), so the FILL path is proven — but to reach the **real** book/pay fields, perception must run through the **extension relay on the user's already-logged-in tab** (`CLARION_ACTUATOR=extension`), never a CDP-attach of the primary profile (per CLAUDE.md). The demo never needs to cross this wall — it's the hard-stop.

**What's REAL vs. NEEDS WORK (the punch-list this walkthrough surfaced):**
1. ✅ **AXTree surfaces the whole data-picker** — 599 interactive nodes; each date×site is a `button` with a self-describing name. The grid is fully groundable (read any cell's date/site/availability from its node).
2. ✅ **Navigation/jump-to-campground** — SITE MAP resolve (present) **and** search fallback (absent), validated on the live planner. Required the parse fix so a bare-string plan survives (`openai_reasoner.plan_goal` / `gemini_reasoner.plan_goal` now accept string subgoals instead of collapsing to the one-subgoal echo) + the SITE-MAP reframe (`_with_site_map`).
3. **Grid pick — ✅ VALIDATED end-to-end** (`probes/recreation_e2e_probe.py`): home→navigate→**grid-axis summary**→**voice→node resolve**→click→effect all pass in one run. The resolve is the **LLM `decide_step` over the full grid** ("Book Boat A June 12" → the exact `Jun 12 … BOAT A` cell, index 23 → click → **"Add to Cart"**), NOT the semantic ranker — calibration showed MiniLM can't separate Boat A/B or June 12/16 (`probes/recreation_resolve_probe.py`), so the ranker would prune the target; the strong decode resolves it cleanly (~4–5s). The grid-axis summary is wired in `summarize_ax_tree`. 🔨 remaining: only the live voice (STT/TTS) leg on the extension tab.
4. ⚠️ **Payment page is behind auth** — perception + fill proven on the login form; the real book/pay fields need the **extension relay on the logged-in tab**. The demo stops at this wall (the consent hard-stop), so it never blocks the run.

---

## 4. Real public-service site targets (ranked)

| Site | Task | Guest (no login)? | AXTree-friendly? | Role |
|---|---|---|---|---|
| **Recreation.gov** `recreation.gov/` → Point Reyes campground `…/campgrounds/233359` | **Book a campsite** | ✅ browse/select; book needs login | ✅ rich — every date×site is a named `button`; **599 nodes**, no truncation (validated) | **Primary "do the task"** — SITE MAP jump → grounded grid readback → pick-a-date → consent hard-stop at the (auth-gated) book step |
| **Pay.gov** `pay.gov/public/form/start/708094624` (Donations to the U.S. Government) | Federal donation / debt | ✅ guest form | ✅ 508-mandated — **but** form inputs inject post-load → `getFullAXTree` perceive bug (see git history) | Backup fillable flow — blocked on the SPA-perceive fix |
| **SMUD** `myaccount.smud.org/unauthenticatedpayment` | Electricity bill | ✅ | ✅ cleanest (2 labeled fields, own domain) | Backup readback + negative ("pay my electric bill") |

**Rules:** drive to the gate, **never complete**; **never enter real credentials**; the book/pay step on Recreation.gov is **behind login** — the demo stops there (the hard-stop), it never reserves or charges.

**Pick one flow:** run the whole thing on **Recreation.gov** — "make a reservation to Point Reyes" → jump to the campground → summarize the availability grid → verifiable-negative on a date → pick a date → hard-stop. One coherent, relatable, fully-grounded flow.

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
