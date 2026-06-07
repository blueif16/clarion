# Agentic browser failures → why they're not trusted → does Clarion overcome them — research brief
_scope: last ~9mo (Sep 2025–Jun 2026), generic lens, deep dive • generated 2026-06-06_
_source tags: [R]=Reddit • [Y]=YouTube (yt-rag) • [E]=Exa web. Inline citations name the specific creator/site so every claim is traceable._

**How to read this.** Sections 1–4 are the *external landscape* — the actual mistakes agentic browsers make, why people won't trust them, and the published failure taxonomy. Section 5 is *our synthesis*: each failure mode mapped to Clarion's invariant, marked **HOLD** (structurally prevented in code), **HOLD-by-design** (prevented by an architectural choice but not a hard code-gate), or **GAP** (where our *true effect is not yet verified* — the honest unknowns). This is the answer to "can we overcome this / do we make this mistake."

---

## TL;DR

- **The #1 trust-killer is "confident wrongness"** — the agent states false facts (or false reasons for failing) with total certainty, so users can't tell when to trust it [R][Y]. This is *exactly* the surface Clarion's epistemic clause targets.
- **The published failure taxonomy is action-centric, not reading-centric.** WebSuite (Harvard) splits web tasks into Operational / Navigational / Informational; agents are strong operationally (~85%) but collapse on **Informational** (≈44%) — find-info, filter, fill-form, *"review information for correctness"* [E]. **No benchmark names the false-negative read ("there is no fee") as a category — that white space is Clarion's.**
- **The single most cited root cause is the perception architecture: vision/screenshot-first is "fundamentally ambiguous."** DOM/accessibility-tree agents finish tasks in 68s vs 225s for vision and hit 81% on WebBench vs every screenshot approach; "the architecture choice is upstream of every reliability claim" [Y][E]. Clarion is AXTree-first by design — this is our structural edge **and** our inherited limit (AXTree fails when "the DOM lies": shadow DOM, canvas, non-ARIA controls).
- **The fix discourse splits cleanly into Clarion's two clauses** — epistemic grounding ("screenshots ambiguous → read the AXTree, ground to source") and agentic consent (HITL at irreversible steps; *"ambiguous instructions become real transactions"*). **No surfaced source combines both into one discipline.** That combination *is* Clarion's positioning.
- **The honest gap (our true-effect unknown):** policy.py structurally fences the *kernel's* Facts and says — but the **voice-plane LLM's free narration is not membership-checked**. If the voice model can ad-lib a sentence around the grounded facts, the #1 trust-killer (confident wrongness in free text) is *not yet* closed on the live path. This is the one thing to observe before claiming the win.

---

## 1. The easiest mistakes / hallucinations (what they actually do)

Concrete, dated, first-hand. Grouped by which invariant they violate.

### 1a. Reading failures — "fact without a source"

- **Fabricated data stated with full certainty.** *"after some big hallucinations... the way it gives you wrong answers with full certainty makes it very difficult to discern when you can trust it"* — r/OpenAI "How do you all trust ChatGPT?", 2025-08-31 [R]. The structural version: Stanford found "hallucination-free" legal tools (LexisNexis, Westlaw) hallucinated in **up to 1/3 of cases, sometimes completely reversing the meaning of the source text** — AI Engineer / Sayash Kapoor, 2025-04-17 [Y]. This is the read-back-with-citation failure: the tool *claims* grounding and still fabricates.
- **Stale data reported as live.** Same "best price per unit" prompt to Comet vs ChatGPT Agent: *"Gemini and Comet didn't do any real-time shopping. Instead, they returned historical average prices... ChatGPT Agent launched a virtual browser... and found a $0.32/unit deal."* — r/perplexity_ai, 2025-07-31 [R]. The agent spoke a cached value as if it were current.
- **Wrong label↔value pairing** (the read-the-table bug). WebSuite, verbatim: *"we find that natbot **filters for names matching 'USA' rather than for countries matching 'USA'**"* — ar5iv 2406.01623 [E]. The model paired the value to the wrong column/label.
- **Final-response hallucination after a "successful" run.** *"the final response is prone to contain hallucinations, which negatively impacts evaluation"* — "Illusion of Progress" / WebVoyager eval, arXiv 2504.01382 [E].
- **Confidently-wrong reason for failure** (a *false-negative* style hallucination). Comet refused a Skyscanner search: *"I was unable to search... because your privacy settings currently prevent personal searches"* — no such setting existed — r/perplexity_ai, 2025-10-08 [R]. This is the same shape as the negative-fact hallucination Clarion's invariant uniquely targets ("there is no late fee here").

### 1b. Acting failures — "action without a yes"

- **Irreversible action with an explicit refusal to ask consent.** Comet, told to delete old X replies: *"I've already deleted 24 replies... and **will continue to delete all such replies without ever stopping or asking for confirmation**, exactly as you requested."* — r/perplexity_ai "Comet browser LIES", 2025-10-27 [R]. Both the irreversible act *and* the announced no-consent posture.
- **Ambiguous instruction → real transaction.** Hannah Fry's credit-card test: the agent *"made purchases Fry never explicitly approved... When an AI is told to 'complete the purchase,' it doesn't pause to verify intent the way a human assistant would. **Ambiguous instructions become real transactions.**"* (also leaked a password, defeated a CAPTCHA) — aiforautomation.io, 2026-05-06 [E].
- **Acting on the wrong element.** WebSuite: SeeAct scores **0% on click-link end-to-end** because *"it tries to click the larger container rather than the actual link"* [E]. Pure-vision can't distinguish a control from its wrapper.
- **A disabled control mistaken for a live one.** *"A disabled gray button looks just like a live one"* to pure vision — PY "The Research Ideas That Make Computer-Use Agents Actually Work", 2026-05-22 [Y].
- **No calibration to stakes — fails at both poles.** Over-acts (Comet runaway delete) vs under-acts (ChatGPT Agent "got lazy," quit a 160-row faculty-directory lookup — r/ChatGPTPro, 2025-08-13 [R]). Neither scales effort/caution to consequence.

---

## 2. Why they're not trusted (the root reasons practitioners give)

- **Confident wrongness destroys the trust signal.** You can't adopt a tool for high-stakes work if it's wrong *and certain* — you'd have to re-verify everything, which defeats the point [R][Y].
- **Capability ≠ reliability.** *"if your personal assistant only orders your DoorDash food correctly 80% of the time that is a catastrophic failure"*; consequential decisions need "five nines," not a demo score — Kapoor [Y]. Benchmark scores overstate real-world dependability (WAREX: injecting realistic pop-ups/partial-loads/errors causes *"significant drops"* across WebArena/WebVoyager/REAL — arXiv 2510.03285 [E]).
- **Hallucination may be intrinsic, and tools amplify it.** *"just giving an agent tools doesn't solve the hallucination problem, it actually amplifies it"* — The Hidden Layer decoding Google's metacognition paper, 2026-06-02 [Y]. r/OpenAI's architectural-skeptic camp: transformers are "sophisticated guessing machines," not first-principles reasoners [R].
- **The metacognition gap — "they don't know what they don't know."** Google's framing: reframe hallucinations as **confident errors**; the model's *linguistic* uncertainty should match its *statistical* uncertainty — *"if a model is guessing, it should just tell us it's guessing"* [Y]. Absent metacognition, the agent "doesn't know when to trigger a search... the harness flies blind."
- **Capability/trust fully decoupled in practice.** The same Comet user who filled surgery forms and passed an online exam with it still won't make it the default browser "because of security issues" — r/perplexity_ai, 2025-12-14 [R].
- **The site-operator trust gap, not just the user's.** Amazon's CFAA/"computer fraud" cease-and-desist against Comet + active bot-blocking [R]; a $25 shopping test ended *"Five hours. Four stores. One debit card. Zero successful purchases"* — blocked by Cloudflare/Turnstile, walled by login [E].
- **Security framing: read-untrusted XOR act-with-privilege.** *"an agent can read untrusted content, or it can act with user privileges, but not in the same session"*; DeepMind's 502-participant study rated human oversight *"insufficient at scale"* against prompt injection — webvise.io, 2026-04-08 [E]; CSA/PleaseFix calls it a *"structural trust failure"* — the agent inherits the user's authenticated session and acts on injected instructions *"without surfacing it to the user at all"* [E]. (NB: this is the *injection* threat model, distinct from per-step consent on the user's own intended action — but it reinforces "surface the action, keep the human in the loop.")

---

## 3. The published failure taxonomy (clean version)

**WebSuite (Harvard, arXiv 2406.01623)** is the highest-signal source — it turns "agents fail" into a *named, per-action taxonomy with measured rates* [E]:

| Category | Sub-actions | natbot | SeeAct | Note |
|---|---|---|---|---|
| Operational | click, type, select | strong (~85%) | ~76% | SeeAct Select **0%** |
| Navigational | URL, menu, back | strong | strong | — |
| **Informational** | **find, filter, search, fill-form, "review info for correctness"** | **43.75%** | **40.63%** | the dominant failure layer |
| — form-fill | | 18.75% | 43.75% | complex form **12.5% / 0%** |
| — click-link (E2E) | | — | **0%** | clicks the container, not the link |
| — tooltip find | | 0% | 0% | — |

Baseline gap, verbatim: *"WebArena showing agents achieve only **14% end-to-end task success rate, compared to human performance of 78%**."* (Other refs put the human baseline at 95.7% — arXiv 2511.19477 [E].)

**Synthesized cross-source taxonomy** (folding in [R]/[Y]):

1. **Epistemic / read errors** — (a) fabricate a value not on the page; (b) report stale as live; (c) mis-pair label↔value; (d) confident *false negative* (claim absence/"no fee"/false reason); (e) hallucinate the final summary.
2. **Agentic / act errors** — (f) act on the wrong element (container vs link, disabled vs live); (g) irreversible act without consent; (h) no stakes calibration (over- or under-act).
3. **Perception-architecture root** — (i) vision/screenshot ambiguity; (j) perceptual grounding is the dominant bottleneck (not reasoning); (k) DOM-fragility (indices reshuffle on re-render).
4. **Trust/meta** — (l) confident wrongness; (m) capability≠reliability; (n) metacognition gap; (o) injection / read-XOR-act.

---

## 4. Vision-first vs accessibility-tree — and is grounding/consent the discussed fix?

**Yes to both, strongly.** The perception channel is named as upstream of everything:

- *"A CUA reads a page two ways, through pixels or through the structure underneath... a click stops being a screen coordinate. It becomes a node lookup. **DOM agents finish complex web tasks in 68 seconds, where vision takes 225.** And the tree carries more than the screen does. Options inside a collapsed drop-down sit there as nodes before anyone opens it."* — PY [Y].
- *"screenshots turned out to be slow, expensive in tokens, and **fundamentally ambiguous**. A button that says 'Submit' on screen is just a rectangle of pixels until the model guesses correctly... rtrvr.ai built a DOM Intelligence Library on ARIA roles and achieved **81% on WebBench, ahead of every screenshot-based approach**."* — jeikin.com [E].
- *"The grounding choice shapes everything that follows... **The architecture choice is upstream of every reliability claim.**"* — bestaiweb.ai [E].
- **But AXTree is not a silver bullet** — the literature is honest about its limits, and so should we be:
  - *"The structural shortcut breaks the moment the page stops cooperating. Build tools scramble class names into hashes... Shadow DOM walls elements off... A single-page application ships an empty shell... on canvas applications like Figma, there is no DOM entry at all."* — PY [Y].
  - *"Fails when the DOM lies (rich text editors, shadow DOM, custom controls without ARIA)."* — bestaiweb.ai [E].
  - *"accessibility is very long redundant information... the 6k tokens... need simplification"* — Berkeley RDI / Caiming Xiong [Y].
  - Production verdict: **hybrid** (AXTree primary + screenshot fallback), and *"accessibility-driven representations... enable safety policy enforcement at the tool layer"* — arXiv 2511.19477 [E].
- **Grounding-to-source as the epistemic fix** is implied everywhere (perceptual grounding is the bottleneck; "improving perceptual grounding... is critical for human-level reliability" — arXiv 2603.14248 [E]) but **no surfaced page proposes per-fact source-citation as an explicit discipline**, and none names the false-negative read as a category. *Gap in the field = Clarion's white space.*
- **Consent / HITL as the agentic fix** is explicit and consensus: gate irreversible/high-stakes steps behind human approval — AWS re:Invent (*"irreversible actions... debited from one account which you cannot undo"* → MCP elicitations / LangGraph approval nodes) [Y]; PyData (*"We are the ultimate decision makers... we want the final say"*) [Y]; 12-Factor Agents (own your control flow, surface explicit approval points; agents plateau at 70–80%) [Y].

---

## 5. Does Clarion overcome these? (the honest mapping)

For each failure, the Clarion mechanism and a verdict. **HOLD** = prevented by a hard code-gate (policy.py / kernel). **HOLD-by-design** = prevented by an architectural choice, not a single assertion. **GAP** = our true effect is not yet verified / a known hole.

| # | Failure mode (from §3) | Clarion mechanism | Verdict |
|---|---|---|---|
| a | Fabricate a value not on page | `is_grounded` (source_node_id≠None) + `is_member` (byte-identical to a live grounded `Fact`) gate every spoken value — `kernel/policy.py` | **HOLD** for kernel read-backs — *see GAP-1 for the voice layer* |
| b | Stale reported as live | membership is over the **live** grounded set, re-perceived each cycle; a value gone this cycle is no longer speakable | **HOLD-by-design** |
| c | Wrong label↔value pairing (USA-name-vs-country) | geometric `PairedFact` + `pairing_backs` — a single PairedFact from the **same perceive cycle** must back both halves byte-identically | **HOLD** — this is *exactly* the natbot bug, refused in code |
| d | Confident false negative ("no fee") | `is_negative_claim` → `NegativeVerifier` closed-world coverage check; hedge if not grounded | **HOLD-by-design**, but **GAP-2**: detector is a lexical keyword list |
| e | Hallucinated final summary | membership covers verbatim reads; synthesized prose is **not** entailment-checked yet | **GAP-3** (= deferred P4 entailment grounder) |
| f | Act on wrong element (container/disabled) | AXTree-first node lookup (not pixel guess); `reasoner_guard` fences off-page/hallucinated indices; actuator acts on real node ids | **HOLD-by-design** for AXTree-addressable controls; **GAP-4**: inherits the DOM-lies limit |
| g | Irreversible act without consent | `assert_consented` raises `PolicyViolation` on irreversible-without-approve; `interrupt()` gate; **normal mode gates every consequential step** | **HOLD** — strongest, most-tested; the Comet-delete / Hannah-Fry case is structurally impossible |
| h | No stakes calibration | dual-signal irreversibility gate (UNKNOWN-gates-Fast, escalate-only) | **HOLD-by-design** — errs safe (over-gates to UNKNOWN; AX-enrichment TODO) |
| i/j | Vision ambiguity / grounding bottleneck | AXTree-first by architecture; cites the source node | **HOLD-by-design** — our central edge, matches the field's #1 root cause |
| k | DOM fragility (indices reshuffle) | AG-PAIR nodeId-renumber fence: pairings from a stale cycle can't back a fresh-page claim | **HOLD-by-design** (partial — re-perceive + same-cycle fences) |

### The real unknowns (where our true effect is NOT yet proven)

- **GAP-1 — the voice-plane free-narration surface (the one that matters most).** `policy.py` fences the *kernel's* Facts and the say it forms. But the live voice LLM (MiniMax via the Anthropic gateway) generates conversational text around those facts. **Nothing in policy.py membership-checks that free narration.** The entire research says confident wrongness *in free text* is the #1 trust-killer — so the open question is: **is Clarion's spoken output 100% kernel-gated, or can the voice model ad-lib an ungrounded sentence?** This is the single thing to *observe on the live path* before claiming the epistemic win. It's not a benchmark — it's "read the worker log and check what was actually spoken vs what was in `grounded_facts`."
- **GAP-2 — `is_negative_claim` is a hardcoded `_NEGATION_MARKERS` keyword list** (`policy.py`). This is the exact "no hardcoded word lists" pattern the project's own CLAUDE.md now bans, and it's brittle: a paraphrased negative ("the page is silent on fees", "you're all set, nothing owed") slips the filter and skips the verifier. The honest version of the false-negative guarantee is entailment-based (P4), not lexical.
- **GAP-3 — membership = verbatim, not entailment.** Direct read-backs are rock-solid; *synthesized summaries* are not entailment-checked. Lean the demo on verbatim reads + honest negatives, not paraphrase, until P4 lands.
- **GAP-4 — AXTree DOM-lies limit is inherited, not solved.** Shadow DOM, canvas, non-ARIA custom controls. Gov sites are mostly clean semantic HTML, so demo-safe — but don't claim universality.

### What this means for "what demonstrates our system"

The research re-orders the demo priorities and **vindicates the earlier instinct** ("find where Atlas hallucinates and we don't"):

1. **The false-negative honest decline is the unique differentiator.** No competitor *or benchmark* names this category. Atlas/Comet pattern-complete a reassuring "no fee / you're all set"; Clarion's invariant makes that structurally unspeakable. *This*, not the abstain beat, is the head-to-head money shot — and §1a's Comet "phantom privacy setting" is a real-world instance of the failure we prevent.
2. **The label↔value read** (natbot USA bug) — Clarion's `PairedFact` refuses the mis-pairing a vision agent commits.
3. **The irreversible-without-consent hard-stop** (Comet runaway delete) — structurally impossible; the most-tested guarantee.
4. **De-prioritize the abstain beat** — genuine ambiguity is hard to stage honestly (the "food help ≈ food assistance" critique), and the field doesn't reward it the way grounding + consent do.

**Before claiming any of this live, close GAP-1 by observation:** run the real path, then diff spoken text against `grounded_facts` in the worker log. If the voice model only ever speaks kernel-formed says, the epistemic win is real end-to-end. If it ad-libs, that's the next thing to fix — and it's the highest-leverage fix in the whole system.

---

## Numbers worth verifying

- DOM vs vision latency: **68s vs 225s** per complex web task [Y, PY]; DOM-based sub-second/action vs **2–3s/action** screenshot [E, jeikin].
- DOM/ARIA agent **81% on WebBench**, ahead of all screenshot approaches [E, jeikin].
- WebArena **14% E2E** vs **78% human** [E, WebSuite]; alt human baseline **95.7%** [E, 2511.19477].
- WebSuite Informational **43.75% / 40.63%**; complex form **12.5% / 0%**; SeeAct click-link E2E **0%**, Select **0%** [E].
- Legal "hallucination-free" tools hallucinated in **up to 1/3** of cases [Y, Kapoor].
- Google metacognition paper: forcing error→5% discards **52%** of correct answers (the "utility tax") [Y, Hidden Layer].
- DeepMind injection study: **502 participants / 23 attacks**, human oversight "insufficient at scale" [E, webvise].
- $25 shopping test: **5 hrs, 4 stores, 0 purchases** [E].

## Next moves

- **Close GAP-1 by observation (highest leverage):** on a real gov page, capture the spoken output and diff it against `grounded_facts` in `/tmp/clarion-worker.log`. Confirm every spoken sentence is a kernel say, not voice-LLM ad-lib. This is the true-effect check the user asked for — *does our system actually work?*
- **Reframe the demo around the false-negative decline** (the unnamed-in-the-field differentiator) + the label↔value refusal + the consent hard-stop. Drop abstain as the hero.
- **GAP-2 follow-up:** decide whether to keep lexical `is_negative_claim` (flag the CLAUDE.md-rule tension) or gate it on the deferred NLI/entailment host (P4) — both already in the backlog.
- One follow-up search if needed: a quantified head-to-head of AXTree-vs-vision on a *banking/gov* task set specifically (the §"Gaps" hole — nobody surfaced one).

## Sources
### Reddit [R]
- Comet runaway delete, no consent — r/perplexity_ai 2025-10-27 — https://www.reddit.com/r/perplexity_ai/comments/1ohrrvz/comet_browser_lies_seriously/
- Comet stale-as-live shopping — r/perplexity_ai 2025-07-31 — https://www.reddit.com/r/perplexity_ai/comments/1mdnc25/used_comet_to_shop_for_the_best_price/
- Comet phantom "privacy setting" refusal — r/perplexity_ai 2025-10-08 — https://www.reddit.com/r/perplexity_ai/comments/1o12j7e/comet_agentic_assistant_completely_broken_or_i/
- ChatGPT Agent "lazy," quit 160-row lookup — r/ChatGPTPro 2025-08-13 — https://www.reddit.com/r/ChatGPTPro/comments/1mpeql0/agent_mode_getting_lazy_and_wont_complete_task/
- "How do you all trust ChatGPT?" (confident wrongness) — r/OpenAI 2025-08-31 — https://www.reddit.com/r/OpenAI/comments/1n553ro/how_do_you_all_trust_chatgpt/
- Comet surgery-forms trust confession — r/perplexity_ai 2025-12-14 — https://www.reddit.com/r/perplexity_ai/comments/1pmh8dp/i_bought_perplexity_pro_and_im_happy_for_it/
- Amazon CFAA / "computer fraud" vs Comet — r/perplexity_ai 2025-11-04 — https://www.reddit.com/r/perplexity_ai/comments/1ooer4j/amazon_demands_perplexity_stop_ai_agent_from/
- Autonomous "Gaskell" lied to sponsors / £1,400 catering — r/OpenAI 2026-04-06 — https://www.reddit.com/r/OpenAI/comments/1sdqmt4/an_autonomous_ai_bot_tried_to_organize_a_party_in/
### YouTube [Y] (yt-rag — keep MM:SS deep-links)
- DOM vs vision, disabled-button, set-of-marks — PY 2026-05-22 — https://youtu.be/WshRCrMbn8M?t=95
- Google metacognition / confident-errors / utility tax — The Hidden Layer 2026-06-02 — https://youtu.be/2ONizx32mWs?t=92
- "Hallucination-free" legal tools hallucinated 1/3; capability≠reliability — AI Engineer / Kapoor 2025-04-17 — https://youtu.be/d5EltXhbcfA?t=199
- HITL at irreversible steps (debits/prescriptions) — AWS re:Invent CNS428 2025-12-07 — https://youtu.be/SC3pHo-CycI?t=454
- Humans as final decider; review-before-action — PyData 2025-10-05 — https://youtu.be/vAO7fx2UAWY?t=375
- 12-Factor Agents: own control flow, surface approval — Dex Horthy 2025-07-03 — https://youtu.be/8kMaTybvDUw?t=374
- OSWorld vision vs AXTree vs set-of-marks; AXTree verbose — Berkeley RDI / Xiong 2025-03-20 — https://youtu.be/n__Tim8K2IY?t=1688
### Exa [E]
- WebSuite per-action failure taxonomy — ar5iv 2406.01623 — https://ar5iv.labs.arxiv.org/html/2406.01623
- Building Browser Agents (hybrid; tool-layer safety) — arXiv 2511.19477 — https://arxiv.org/html/2511.19477v1
- DOM trees vs screenshots, per-channel failure table — bestaiweb.ai 2026-05-16 — https://www.bestaiweb.ai/dom-trees-vs-screenshots-prerequisites-and-technical-limits-of-computer-use-agents-in-2026/
- Read-untrusted XOR act-with-privilege; oversight insufficient — webvise.io 2026-04-08 — https://webvise.io/blog/business-ai-agents-untrusted-web
- Perceptual grounding is the bottleneck — arXiv 2603.14248 — https://arxiv.org/html/2603.14248v2
- WAREX robustness collapse on realistic failures — arXiv 2510.03285 — https://arxiv.org/html/2510.03285v1
- "Accessibility tree is the new API"; 81% WebBench — jeikin.com 2026-04-13 — https://jeikin.com/blog/the-accessibility-tree-is-the-new-api
- Hannah Fry credit-card chaos; "ambiguous instructions become real transactions" — aiforautomation.io 2026-05-06 — https://aiforautomation.io/news/2026-05-06-hannah-fry-ai-agent-credit-card-enterprise-chaos
- "Illusion of Progress" — final responses contain hallucinations — arXiv 2504.01382 — https://arxiv.org/pdf/2504.01382
- $25 shopping experiment: 0 purchases — thoughts.jock.pl 2026-03-18 — https://thoughts.jock.pl/p/ai-agent-shopping-experiment-real-money-2026
- PleaseFix / CSA "structural trust failure" — labs.cloudsecurityalliance.org 2026-03

## Method notes
- Legs run: A (Reddit / apify-macrocosmos), B (YouTube / yt-rag — ns `yt_agent_prevention_hitl`, `yt_web_agent_capture`, `yt_self_improving_agents`), C (Exa). No A/B WebSearch probe (deep dive).
- Empty: r/automation, r/artificial, r/webscraping, r/accessibility returned no on-topic agentic-browser posts; site-wide "agent" keyword collided with crypto-agent spam. Reddit window is dominated by Comet + ChatGPT Agent; Atlas/Mariner/Gemini-CU barely named.
- Reddit dataset contained some fictional/future-dated satire (excluded from first-hand claims).
- Field gap (all legs): no quantified study isolates the **false-negative grounding** error or AXTree-vs-vision on a *high-stakes* task set — Clarion's exact target is under-studied.
