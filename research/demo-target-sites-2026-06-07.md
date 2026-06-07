# Real-life government demo sites + the hardest-website map — research brief
_scope: live-verified 2026-06-07 (Exa + WebSearch, fetched to confirm guest-pay paths) • for the Clarion demo: real .gov sites, drive-to-the-gate, no real money_
_Companion: `docs/clarion-demo-script.md` (the run-of-show that uses these)._

## How to read this
Two questions: **(1) which real government / public-utility sites let us run a navigation → form → payment flow** (up to the consent hard-stop — we never complete a real charge), and **(2) which website types must we never touch live.** Picks favor: a **guest / no-login path**, **semantic HTML + ARIA** (AXTree-rich, not an SPA/canvas), payment that **stays on the site's own domain** (no third-party iframe), and a relatable high-stakes task.

---

## 1. Ranked real-site shortlist (guest-pay verified by fetch)

**Tier 1 — clean guest-pay, semantic form (best targets):**
1. **Pay.gov** — `https://www.pay.gov/public/home` → "Pay My Debt" / VA Medical Copay — the **canonical federal form + payment** — guest pay **YES**, semantic 508-mandated HTML, **fillable guest forms** (form → payment → review → confirmation). *Best "do the task" site — you can actually fill a guest form on camera and reach a real submit/consent gate.* Caveat: completing needs a real debt → you stop at the gate anyway (which is the plan).
2. **SMUD** — `https://myaccount.smud.org/unauthenticatedpayment` — pay an **electricity bill** — guest pay **YES**, the **cleanest** AXTree target (two labeled fields on a dedicated guest URL, own domain, no SPA/iframe). *Best relatable readback + verifiable-negative site; field validation blocks deep fill without a real account.*
3. **NYC CityPay** — `https://a836-citypay.nyc.gov/` — **property tax / parking ticket** — guest pay **YES** ("Pay as a Guest", "No registration required"), plain server-rendered selector. *High-stakes, very relatable civic montage clip.*
4. **Seattle City Light / SPU** — `seattle.gov/utilities/...` — electricity/water — guest pay **YES** (account + ZIP only, no convenience fee), city .gov semantic. Clean backup.
5. **Denver Water** — `https://www.denverwater.org/pay-my-bill` — water — guest pay **YES** ("One-Time Guest Payment"), semantic. Clean backup.

**Tier 2 — guest-pay but a 3rd-party processor redirect (AXTree risk — the card step leaves the .gov page):**
- **Austin Energy** "Quick Payment" (No Login) and **CPS Energy** "One Time Payment" — both hand card entry to **Paymentus** on a different host. The .gov page is clean; the payment frame is a separate DOM. **Avoid** unless you validate the Paymentus host separately.

**Tier 3 — login/friction (flag):**
- **IRS Direct Pay** — guest, no enrollment, BUT identity-verification (prior-year AGI) is hard to fake + a **nightly maintenance outage** (verified). Daytime only, can't complete.
- **EFTPS** — **no new individual enrollments since Oct 17 2025** → drop it.
- **myNCDMV (PayIt)** — a rare guest-pay DMV, but a **React SPA** (the AXTree-risk category) → hard-mode, not the headline.
- **LADWP / Colorado Springs Utilities / MLGW** — one-time pay is **login-gated** → avoid.

**State benefits (SNAP/Medicaid/SSA/Medicare):** mostly application/eligibility flows, not bill payments → use as read/form demos, not payment demos.

### Top 3 picks
1. **Pay.gov** — the only one where you can actually fill a guest form AND reach a real consent gate on camera.
2. **SMUD** — the relatable "pay my electric bill" readback + negative.
3. **NYC CityPay** — property tax / parking, the relatable civic montage.

### Hard rules for the recording
- Drive to the gate, **never complete**; **never enter real credentials**; use obviously-fake values (test card `4111 1111 1111 1111`).
- Pick sites whose payment stays **on their own domain** (Tier 1) — never a Paymentus/Stripe iframe step.

---

## 2. The hardest website types — NEVER touch live

Each is either **pixel-only** (AXTree empty → nothing to cite) or **nondeterministic** (the demo-killer):
- **Canvas apps** — Google Docs/Sheets, Figma/FigJam, Canva, Miro, maps. No DOM, no a11y nodes. ([tianpan.co](https://tianpan.co/blog/2026-04-19-browser-agents-dom-fragility-production); [arXiv 2511.19477](https://arxiv.org/html/2511.19477v1))
- **Late-hydrating SPAs** — empty shell + async fill; fails **intermittently** (a Browser-Use agent on panerabread.com reported "DOM completely empty" one run, full checkout the next). Intermittent = worst for a recording.
- **Closed shadow DOM** — Salesforce Lightning (obfuscated scope tokens); GitHub/YouTube ship web components. ([samelogic.com](https://samelogic.com/blog/how-agents-see-the-web))
- **Custom non-ARIA widgets** (divs-as-buttons) — the AXTree's signature blind spot; the tree under-represents the page.
- **Cross-origin iframes** — embedded Stripe/PayPal/**Paymentus** payment frames, consent banners.
- **CAPTCHA + selfie/liveness 2FA** (ID.me, Login.gov doc-upload) — *themselves inaccessible to blind users*; never on the critical path → **stop before any identity-verification step**.
- **Anti-bot walls** (Cloudflare Turnstile, PerimeterX, DataDome, Akamai — airlines, ticketing, StockX, Indeed). **Key nuance:** these sink the autonomous Playwright path; the **extension transport runs in the user's own authenticated Chrome session**, so anti-bot/2FA barely touch the live product demo. ([hellworld.io](https://hellworld.io/blog/anti-bot-landscape-2026))

## 3. AXTree-FRIENDLY sweet spot — where you WIN

**Server-rendered government / public-utility forms** on Section-508 / 2024-ADA-Title-II / WCAG-2.1-AA-mandated sites. Mandated semantics → AXTree-rich → **you ground every fact on a real source node while a vision agent guesses coordinates and a screen reader linearizes the same tree slowly and mis-surfaces controls.** Gov *looks* hard (gov/benefits rank among the most barrier-laden tasks) but *is* winnable — that contrast is the head-to-head. Pick the **form-fill / informational** flow, never the identity-verification or CAPTCHA step. ([WebAIM Million 2025](https://webaim.org/projects/million/2025): gov sites = 27% fewer errors than average; [ADA.gov Title II rule](https://www.ada.gov/resources/2024-03-08-web-rule/): deadlines Apr 2027/2028.)

## 4. Task-barrier ranking (cite correctly)
- **Travel booking ≈ banking ≈ job apps > e-commerce checkout > food > gov info/benefits content** (but gov **identity-verification** spikes back to hardest).
- **CORRECTION:** the "91–94%" travel figures are **barrier-ENCOUNTER rates** (AFB survey), not task-failure rates — say "94% hit a barrier booking flights."
- **CORRECTION:** the "68 min per barrier on gov sites" figure is **unverified** in any source — drop or source it.
- Safe to cite: **54.3%** blocked from bill pay, **76.5%** had to ask another person, **65%** blocked on inaccessible forms (Aira), unlabeled inputs = WebAIM #1 problematic.

## Sources
- DOM fragility / canvas / hydration: tianpan.co/blog/2026-04-19-browser-agents-dom-fragility-production · arXiv 2511.19477
- Shadow DOM / iframe walls: samelogic.com/blog/how-agents-see-the-web · blazemeter.com/blog/web-components-shadow-dom
- Anti-bot vendor→site map: hellworld.io/blog/anti-bot-landscape-2026 · scrappey.com (Cloudflare coverage)
- WebAIM Million 2025 (gov best sector, home-page-only caveat): webaim.org/projects/million/2025
- AFB barrier rates / Login.gov VPAT / ADA Title II rule: afb.org · login.gov VPAT · ada.gov/resources/2024-03-08-web-rule
- Site fetches (guest-pay verified): pay.gov, myaccount.smud.org, a836-citypay.nyc.gov, denverwater.org, seattle.gov, coautilities.com, cpsenergy.com, directpay.irs.gov

## Method notes
- Two parallel scout agents (Exa + WebSearch), fetched to confirm guest-pay paths + semantic-vs-SPA. Gap: the exact YC Conversational AI June-6–7 event has no winners posted yet (re-scan in 1–3 days).
