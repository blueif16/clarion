# Demoing prevention — showcase research brief
_scope: 2024–2026, agentic-web + accessibility lens, deep dive (3 legs + YouTube ingest) • generated 2026-06-06_
_source tags: **[R]**=Reddit • **[Y]**=YouTube (yt-rag, namespace `yt_agent_prevention_hitl`, 13 videos ingested for this run) • **[E]**=Exa web. Inline citations name the specific source._

> **The reframe this brief serves:** Clarion's value is not "navigation works." It is **failure-prevention** — never letting the page state get corrupted, never taking the user somewhere wrong, never speaking something that isn't on the screen. The give-up was the system *trying* to do this (badly). The demo is **"watch it refuse to do the wrong thing and keep you on track."** This brief shows that (a) every serious agent vendor now demos exactly this, (b) there is a known *craft* to making restraint compelling, and (c) Clarion owns specific whitespace none of them cover.

## TL;DR — the demo thesis
1. **Confirm-before-the-irreversible-step is the industry's headline safety beat.** Six independent vendors stage the *same* moment: Mariner "asks me if it should proceed to checkout… keeping a human in the loop" [E,Y Google DeepMind]; Operator "asks for confirmation… reduced the risk of model mistakes by ~90%" [E system card]; ChatGPT agent "you're always in control… asks permission before actions of consequence" [E]; PyData "I'm about to buy 10 stocks for $15. Approve or reject?" [Y]; Anthropic "ask a human to confirm… financial transactions, accepting cookies, agreeing to ToS" [E].
2. **The compelling demo shows a CAUGHT failure, not a completed task.** IBM's harness talk literally shows the agent "clicks upvote and considers it a success — it doesn't verify… look, it lies," then a deterministic verify step "stopped lying" [Y]. Claude for Chrome demos *catching a phishing email* that tries to make it delete inbox items [E]. The narrated *"I stopped because…"* is the moment.
3. **Restraint must be SELECTIVE — per-step prompts are anti-safety.** Anthropic's own telemetry: users "approved roughly 93% of permission prompts… the more approvals a user sees, the less attention they pay" [E "How we contain Claude"]. Stop on the *one* consequential step, free-run the rest.
4. **Grounding must be verifiable in-situ, not a bare citation.** PageGuide: "a link does not guarantee the referenced source actually supports the generated claim… grounding every answer directly in the live HTML DOM" [E]. This is Clarion's epistemic clause, externally validated.
5. **Abstaining impresses precisely because models don't do it by default.** "LLMs by default rarely clarify or abstain, ignoring uncertainty" [E BAG]; capability ≠ reliability and "for consequential decisions you need reliability" [Y Sayash Kapoor]. Clarion's "say when it can't find it" is the non-default behavior that reads as trustworthy.

---

## Part 1 — What the major products actually showed (prevention demos)

### OpenAI — Operator / ChatGPT agent / Atlas
- **Operator System Card** [E https://cdn.openai.com/operator_system_card.pdf] — the most *quantified* prevention framing, and the template for Clarion's metrics slide. Safety = three stacked layers: **proactive refusals, confirmation prompts before critical actions, active monitoring**, with the policy **keyed to reversibility** ("the ease of reversing any negative outcomes"). Numbers: confirmations **−~90% mistake harm**; **92% confirmation recall** (607 tasks / 20 risky categories); **94% refusal recall**; prompt-injection monitor **99% recall / 90% precision**; **Watch Mode** auto-pauses when the user goes inactive on sensitive sites; **fully restricts** stock buy/sell.
- **ChatGPT agent** [E openai.com/index/introducing-chatgpt-agent; Y https://youtu.be/1jn_RpbPbEc?t=735] — "**you're always in control**." Trained to **ask clarifying questions (not every time)**, be **interruptible**, **confirm at the last step** ("review the email draft… embarrassing typos" before send), and use **takeover mode** so credentials go to the human, not the agent.
- **Atlas agent mode** [E help.openai.com/.../12628199] — **pauses on sensitive sites** (banks), user-set **approval checkpoints**, and the hardening note: "**Avoid overly broad prompts** like 'review my emails and take whatever action is needed.'"

### Google — Project Mariner / Jarvis
- **The canonical human-in-the-loop demo** [E,Y Google DeepMind https://youtu.be/_uBg6syzXhk?t=91]: agent adds a print to an Etsy cart, then "**asks me if it should proceed to checkout… a great example of how we are keeping a human in the loop**… there's no need to check out… hands back control." The *emotional core*: the user says **no** and the agent yields.
- **Design-by-restriction** [E techcrunch 2024-12]: Mariner "**cannot check out**, **won't accept cookies**, **won't sign a terms of service**… to give users more control," and "**reverts back to the chat window, asking for clarification**" (e.g. how many carrots).

### Anthropic — Computer Use / Claude for Chrome
- **Tool-doc rule** [E platform.claude.com computer-use-tool]: "**Ask a human to confirm decisions that might result in meaningful real-world consequences… such as accepting cookies, completing financial transactions, or agreeing to terms of service**." Prompt-injection classifier "**automatically steer[s] the model to ask for user confirmation**."
- **Self-verification** [E; echoed Y YC Decoded]: "After each step, take a screenshot and carefully evaluate if you have achieved the right outcome… Only when you confirm a step was executed correctly should you move on."
- **Claude for Chrome** [E claude.com/blog/claude-for-chrome]: action confirmations for **publish/purchase/share**; *demoed catching a phishing attack*; injection success **23.6%→11.2%**, browser challenge set **35.7%→0%**.
- **The key caution — approval fatigue** [E anthropic.com/engineering/how-we-contain-claude]: "users approved roughly **93%** of permission prompts"; OS sandbox cut prompts **−84%**; "experienced users supervise the agent **only when it goes off track**."

### PageGuide & touch-browser — grounding + abstain (closest to Clarion)
- **PageGuide** [E pageguide.github.io] — "**a link does not guarantee the referenced source actually supports the generated claim**… grounding every answer directly in the live HTML DOM, so users can **verify evidence in-situ**." Inline `[N:"exact phrase"]` verbatim spans; **guide mode advances one step at a time, only when the user clicks Next**; hide/act mode is consent-gated. User study N=94: completion 23%→53%, accuracy 30%→56%. It *indicts competitors on a slide* (Atlas "does not highlight supporting evidence"; Browser Use "users cannot verify or intervene").
- **touch-browser** [E github.com/nangman-infra] — four conservative verdicts incl. **`insufficient-evidence`**; "a **safe unresolved path for borderline claims instead of bluffing**"; "**read-first by default**."

### Accessibility / blind-user voice copilots (the adjacent field)
- **AccessBrowse** [E,Y https://youtu.be/1BBzOFUTdKw] — voice copilot that "**sees what a person sees**" via vision (DOM screen readers "break 20–30% of the time on SPAs," "96% of homepages fail WCAG"), **reads results back**, user can **interrupt anytime**.
- **Noor** ("**narrates every action aloud so the user stays in control**"), **AccessBot** (**barge-in**, step-by-step voice form-filling), **Spectra** ("**talks while it works**") [E GitHub, 2026].
- **Gap = Clarion's whitespace:** these are 2026 hackathon projects; **none publishes an explicit abstain / grounding *invariant*** the way Clarion does, and none demos a **verifiable negative** ("there is no late fee here").

### Benchmarks — capability, not prevention (a gap to name honestly)
WebVoyager / WebArena measure task success, not restraint [E,Y]; Mariner cites 83.5% WebVoyager. There is **no named "abstention/grounding benchmark for web navigation"** — the prevention axis is demoed, not yet benchmarked. Clarion can define its own *recall-on-safe-behavior* metric.

---

## Part 2 — The demo-craft: what makes a "we prevent the failure" demo land
1. **Quantify the restraint.** Lead with a *recall number on the safe behavior* (Operator's 92% confirm / 94% refuse / −90% harm), not a task-success number [E]. Restraint you can measure reads as engineering, not luck.
2. **Show a CAUGHT failure.** The agent narrating *why* it stopped — IBM's "look, it lies" → verify step → "it stopped lying" [Y]; Claude refusing the phishing email [E] — is more convincing than any success.
3. **Key the gate to reversibility.** Confirm on irreversible/state-changing steps, fully restrict the worst, free-run the rest [E Operator]. Principled, not paranoid.
4. **Stage the "asks back" beat, then say no.** "Should I proceed to checkout?" → user declines → agent hands back control [Y Mariner]. The user's *no* is the emotional payload.
5. **Selective > per-step.** Cite approval fatigue (93% blind-approve) [E] and stop on the *one* consequential step — a wall of "allow?" dialogs is safety theater.
6. **Ground in-situ, verifiably.** Highlight the exact on-page span, don't trust a bare citation [E PageGuide].
7. **State uncertainty in task terms.** "The request is ambiguous" / "current evidence is missing," **not** "I feel unsure" [E MARC]; abstaining impresses because it's non-default [E BAG].
8. **Demo on real, messy pages.** Practitioners distrust slick demos on clean sites ("the demos are slick… then you actually try to build one… it gets ugly fast") [R]; show a gov/bank page, not a toy.

---

## Part 3 — Why restraint is the trustworthy thing (the evidence)
- **Capability ≠ reliability** [Y Sayash Kapoor https://youtu.be/d5EltXhbcfA?t=836]: "reliability means consistently getting the answer right each and every single time… for consequential decisions you need reliability." (Devin: 3/20 real tasks.)
- **Agents over-claim success; a *separate* verifier must catch it** [Y IBM harness; Y YC Decoded screenshot-to-check] — verification is a distinct layer from the generator (mirrors Clarion's code-side done-check + policy).
- **Metacognition / honest uncertainty** [Y Hidden Layer]: "honest about its own limitations… that honesty is what actually builds reliable utility."
- **Failure canon to invoke** [R]: Project Vend / "Claudius" — Claude ran a store, "**hallucinated payment details and gave discounts to nearly everyone**," "sold items at a loss" — the cautionary tale of a confident, ungrounded agent. The Yellowstone wander (Anthropic's own honest demo of Claude going off-task) [R].
- **Partnership-not-replacement framing wins** [R]: "the most positive responses come when I frame it as 'this agent will handle X so you can focus on Y'… People want to feel empowered, not eliminated."

---

## Part 4 — Clarion: the demo narrative + the whitespace we own

### The narrative (prevention is the hero, on a real high-stakes page)
A blind user on a real gov/healthcare/bank page. Four beats — each a *failure a naive agent would commit, that Clarion prevents*:
1. **Grounded read-back, incl. a verifiable negative.** "Here's what's actually on this page… and there's **no late fee listed here**." Contrast: a normal assistant guesses. *(Epistemic guarantee — no vendor demos a sourced negative; whitespace.)*
2. **Abstain on ambiguity (THE hero beat — your gov-page case).** User asks for "food assistance"; the page has *Food assistance* and *Food safety*. Clarion: "**I can see two — which did you mean?**" instead of silently opening the wrong one. *(This is what the give-up should have been.)* [grounded by Y Operator/ChatGPT clarifying-questions; E MARC/BAG]
3. **Consent before the consequential step.** "**I'm about to open Food assistance — yes?**" → acts only on yes; "I won't submit this payment without your yes." [Y Mariner/PyData; E Operator]
4. **Refuse to corrupt state / lead astray.** When uncertain or when an action would leave the user lost, Clarion **refuses + explains + offers what it can see** — "I won't take you somewhere I can't verify. Here's what's here." *(The guarantee: we keep you on the right track.)*

### The metric to show (borrow Operator's framing)
Report **recall-on-safe-behavior**: how often Clarion correctly **asks/abstains on ambiguous or ungrounded cases** and **gates consequential actions** — the prevention analog of 92%/94%/−90%. Capability (did it complete?) is secondary.

### Whitespace Clarion uniquely owns (none of the surveyed systems do all three)
- **A spoken *verifiable negative*** ("no late fee here") — fenced by the NegativeVerifier. No vendor demos this [E gap].
- **An explicit, stated *invariant* for a blind user** ("no fact without a source, no action without a yes") — accessibility copilots narrate actions but publish no grounding/abstain guarantee [E,R gap].
- **Consent + grounding unified** — others gate *actions* (irreversibility) OR cite *facts*; Clarion does both, with the human's ear as the final check (PageGuide grounds but doesn't voice; Operator gates but doesn't ground a read-back).

---

## Numbers worth keeping
- Operator: **−90%** mistake harm via confirmations; **92%** confirm recall (607 tasks/20 cats); **94%** refusal recall; injection monitor **99%/90%** [E].
- Anthropic: **93%** of permission prompts blind-approved; **−84%** prompts via OS sandbox [E]; Claude-for-Chrome injection **23.6%→11.2%**, **35.7%→0%** [E].
- PageGuide user study: completion **23%→53%**, accuracy **30%→56%**, Ctrl+F usage **−80%** [E].
- Accessibility: DOM screen readers fail **20–30%** on SPAs; **96%** of homepages fail WCAG [Y AccessBrowse].
- Reliability reality: Devin **3/20** real tasks [Y]; "agents are a mess… demos are slick, real builds get ugly" [R].

## Next moves
- **Build the demo around Part 4's four beats** — beat 2 (abstain-on-ambiguity) is the literal gov-page "Food assistance vs Food safety" case from the plan (P3). Implement P3 and you have the hero shot.
- **Add the recall-on-safe-behavior metric** to the panel/instrumentation so the demo can quote a number (Operator-style).
- **Claim the verifiable-negative + stated-invariant whitespace** explicitly in the pitch.
- Optional follow-up search (gap): first-party Adept/Jarvis prevention footage; a clean WebVoyager demo; more blind-user copilot demos via "#GeminiLiveAgentChallenge" youtu.be links.

## Sources
### Reddit [R]
- Gemini 2.5 Computer Use (per-action review + confirm) — https://www.reddit.com/r/AI_Agents/comments/1o178u4/
- OpenAI Operator launch thread — https://www.reddit.com/r/OpenAI/comments/1i89lt0/
- Project Vend / Claudius failure canon — https://www.reddit.com/r/OpenAI/comments/1lnzg0d/
- "I build AI agents… it's a mess" (demo vs reality) — https://www.reddit.com/r/AI_Agents/comments/1ojyu8p/
- GUI grounding for accessibility — https://www.reddit.com/r/accessibility/comments/1impis6/
- "turn hallucinations into an honest I don't know" — https://www.reddit.com/r/LocalLLaMA/comments/1tot20j/
- Yellowstone wander — https://www.reddit.com/r/OpenAI/comments/1g9x7du/
- Magentic Marketplace (agents manipulable) — https://www.reddit.com/r/AI_Agents/comments/1otpe8s/
### YouTube [Y] (namespace yt_agent_prevention_hitl; deep-links keep MM:SS)
- OpenAI — Demonstrating Operator — https://youtu.be/gYqs-wUKZsM?t=182
- Google DeepMind — Project Mariner demo — https://youtu.be/_uBg6syzXhk?t=91
- OpenAI — Introduction to ChatGPT agent — https://youtu.be/1jn_RpbPbEc?t=735
- IBM/Tejas Kumar — Harnesses in AI (verify-caught-the-lie) — https://youtu.be/C_GG5g38vLU?t=834
- PyData — Secure HITL Interactions (approve/reject) — https://youtu.be/vAO7fx2UAWY?t=1016
- AccessBrowse — voice copilot for blind users — https://youtu.be/1BBzOFUTdKw?t=92
- Sayash Kapoor — capability vs reliability — https://youtu.be/d5EltXhbcfA?t=836
- Royal Hansen (Google) — agentic AI safety principles — https://youtu.be/32qlZOXHips?t=280
- The Hidden Layer — metacognition / honest uncertainty — https://youtu.be/2ONizx32mWs?t=273
- AWS re:Invent — HITL checkpoints / progressive autonomy — https://youtu.be/SC3pHo-CycI?t=454
- Dex Horthy — 12-Factor Agents — https://youtu.be/8kMaTybvDUw?t=818
- Merantix/Intercom — Building Reliable AI Agents — https://youtu.be/YuTn29Y04KM?t=1641
- Y Combinator — Claude Computer Use decoded — https://youtu.be/VDmU0jjklBo?t=184
### Exa [E]
- Operator system card — https://cdn.openai.com/operator_system_card.pdf · ChatGPT agent — https://openai.com/index/introducing-chatgpt-agent/ · Atlas agent mode — https://help.openai.com/en/articles/12628199
- Project Mariner — https://techcrunch.com/2024/12/11/google-unveils-project-mariner-ai-agents-to-use-the-web-for-you/
- Anthropic Computer Use tool — https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool · Claude for Chrome — https://claude.com/blog/claude-for-chrome · How we contain Claude — https://www.anthropic.com/engineering/how-we-contain-claude
- PageGuide — https://pageguide.github.io/ · touch-browser — https://github.com/nangman-infra/touch-browser
- BAG (abstain/clarify) — https://arxiv.org/html/2605.25831 · MARC draft — https://www.ietf.org/archive/id/draft-c4tz-marc-02.html
- Accessibility copilots — github.com/aliahmedd24/noor · github.com/sgharlow/accessbrowse · github.com/zacnider/accessbot

## Method notes
- Legs: A Reddit (126 threads, loose keyword filter — site-wide call returned off-topic, flagged) · B YouTube (13 videos ingested this run → `yt_agent_prevention_hitl`, ~60 chunks searched) · C Exa (24 pages) + a YouTube-discovery Exa scout.
- **YouTube ingest WAS run this session (user-authorized):** 13 curated videos across OpenAI/Google/Anthropic/AI-Engineer/PyData/AWS/accessibility → namespace `yt_agent_prevention_hitl` (global corpus, benefits future runs).
- Gaps: no clean Adept/Jarvis or WebVoyager prevention demo; blind-user copilot demos mostly on Devpost/short links (omitted for lack of resolvable watch URLs); no vendor demos a *verifiable negative* (Clarion whitespace).
