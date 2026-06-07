# Hackathon winners → demo effects + winning stories to copy (and one-up) — research brief
_scope: last ~6 months (Dec 2025 → Jun 2026), two lanes (accessibility/blind-tech voice AI + autonomous "smart task" browser agents), deep dive (3 legs) • generated 2026-06-07_
_source tags: **[R]**=Reddit • **[Y]**=YouTube (yt-rag) • **[E]**=Exa web. Inline citations name the specific creator/site so every claim is traceable._

## How to read this
The goal was NOT "what's the best architecture" — you already have the architecture. It's **"what did awarded projects DEMO and SAY, so Clarion can copy the effect and do it one-better."** Section order: the competitive map (named winners) → the demo EFFECTS that win (each mapped to what Clarion already has) → the STORY patterns that win → the white space you own → demo-craft anti-patterns → concrete "copy-this-then-beat-it" for the Clarion demo. The one-paragraph version: **your direct competitors exist (Noor, AccessBrowse, Orbit, Spectra, LetsHelp), they all converged on your exact architecture, and not one enforces grounding in code — they're all system-prompt fences. Grounding is meanwhile a literal scored judge rubric item. That gap is the whole game.**

---

## TL;DR
1. **The blind-voice-navigator lane is CROWDED and converged.** Noor, AccessBrowse, Orbit, Spectra, IAN, EyeGuide, GozAI, Ujala — all 2026 hackathon entries, all "vision/AXTree → narrate every action → barge-in → consent before destructive." Clarion is NOT alone, and "blind-first voice copilot" alone is not differentiation. [E]
2. **But NONE of them enforces grounding in code — all rely on system-prompt-only fences.** [E, synthesized across Noor/AccessBrowse/Spectra]. Clarion's code-gated membership + the **verifiable negative** ("no late fee here") is the one thing the whole field lacks. This is your white space, confirmed.
3. **Grounding is a SCORED rubric item, not just a nicety.** The Gemini Live Agent Challenge (11,878 entrants) judged: *"Does the agent avoid hallucinations? Is there evidence of grounding?"* [E cloud.google.com]. Judges reward exactly your thesis — and you do it harder than anyone.
4. **The winning demo EFFECTS are things you ALREADY have wired:** make-the-reasoning-visible (Operator's "list of dots," ChatGPT-agent's overlaid chain-of-thought), the **consent beat as the emotional peak** (Mariner "asks before checkout," PRESENT!'s Allow/Deny gate, Nimbus "Handoff Points"), and the killer one — **"catch the agent lying" live** (IBM/Tejas Kumar: a deterministic verifier proves the action *actually failed* and the agent "stopped lying" on stage). That last one IS your grounding invariant performed live. [Y]
5. **The winning STORY is "the real pain isn't reading, it's DOING"** (Noor, verbatim [E]) + **narration pacing so the user is "never left in silence"** (Noor [E]) + **highest-stakes hands-free wins** (ORION, a surgical voice co-pilot "without breaking scrub," took the Grand Prize [E]). Lead with doing/independence; show the grounding.
6. **Flashy autonomy is a demo-killer.** Atlas's launch — "no Wow moments… buggy/slow" — is the cautionary tale [R]; practitioners loudly prefer human-in-the-loop over "Jarvis." Your honest-decline degradation ("I won't guess") is the *safe* live failure mode. [R]
7. **Your exact event (YC Conversational AI Hackathon, June 6–7) has no winners posted yet** — it's concluding now. You can't copy its winners; you CAN copy the broader field's effects, and re-scan Devpost/Moss/LinkedIn in 1–3 days. [E]

---

## 1. The competitive map — named winners, both lanes

### Lane A — accessibility / blind & low-vision voice navigators (your direct lane)
| Project | What it does | Demo effect / wow-moment | Story / hook | Stack | Placement |
|---|---|---|---|---|---|
| **Noor** [E devpost] | Vision-first voice web-navigator for blind users; "look at the screen and do it for you" | **Narration-pacing as a feature** — tool start/end + live screenshots so the user is *never left in silence* | *"The real pain isn't reading, it's DOING… transactional workflows require sighted interaction at every step"* | Gemini Live | Gemini Live Challenge (UI Navigator field) |
| **AccessBrowse** [Y,E] | Voice Chrome extension on Gemini Computer Use; maps screen to a 1000×1000 grid | **"Live stress test across the web's most complex interfaces. One agent, zero sight-specific configuration"** (Wikipedia→Google→Amazon→Zillow) | "Screen readers approach the problem backwards… break 20–30% of the time… 96% of homepages fail WCAG; we cannot wait for every developer to fix their code" | Gemini Computer Use + Cloud Run | Gemini Live Agent Challenge entry |
| **Orbit** [E github] | Voice-first Windows/OS control for blind/elderly/motor-impaired; sees screen as pixels | **Single press-and-speak collapses a whole click/type/scroll workflow**; speaks result back | "Actually *sees* the screen the way a sighted person does" | Gemini (vision) + DeepSeek-V3 + Playwright/PyAutoGUI + ElevenLabs | **claims Accessibility + Overall + Best ElevenLabs + Best Featherless + Best Gemini** |
| **LetsHelp / Auralis** [E github] | Screen-share tech-support copilot for seniors + hands-free desktop automation | AI "sees" your screen via vision and **walks you through (or auto-clicks)** in a warm voice | *Empathetic, patient tone* "for users who may feel frustrated or embarrassed about needing help" | Gemini 2.0 Flash + ElevenLabs + LiveKit screen-share + Deepgram | **2nd Place Overall + Gemini Track, HH26** |
| **ASL→speech phone agent** [E yt] | MediaPipe converts ASL hand movements to speech to make calls/book appts | Sign-language → live phone call | Accessibility for the speech-impaired | MediaPipe + Cekura + Claude Code | won **YC interview + RTX 5080** (prior YC voice hackathon) |
| **ORION** [E] | Voice-directed **surgical** co-pilot (hands-free, highest-stakes) | Surgeon speaks naturally, gets answers + live data + visual assist **"without breaking scrub"** | Highest-stakes hands-free voice | Gemini Live API + ADK + GCP | **GRAND PRIZE, Gemini Live Agent Challenge** |

### Lane B — autonomous / "smart task" browser agents
| Project | What it does | Demo effect / wow-moment | Story / hook | Placement |
|---|---|---|---|---|
| **Browser Brawl** [E minimax] | Arena where **two browser agents fight on a live site** (one does the task, one interferes) → traces for agent eval | Live **agent-vs-agent combat** on a real website, built in <1 day | Turns flaky agent-eval into a spectator sport | **Winner, YC Browser-Use Hackathon ($180K+ pool, Feb 28)** |
| **Moonwalk** [E] | Hands-free voice desktop copilot controlling mouse+keyboard | "Book flights / manage spreadsheets while you sit back and speak," **remembers preferences** | Ambient co-pilot with memory | **UI Navigator winner, Gemini Live Challenge** |
| **Wand** [E] | Voice + pointer-aware browser agent | **"Point at the screen and say 'play this video'"** — gesture+speech fusion, no mouse | Multimodal control | **Best Multimodal UX, Gemini Live Challenge** |
| **Nimbus** [E devpost] | Gemini-3 browser agent | **"Handoff Points": stops at CAPTCHA / 2FA / a $500 ad-spend confirmation** — "hey, does this look right?" | *"Trust comes from collaboration, not raw capability"* | Devpost (strong craft; placement unconfirmed) |
| **TaskPilot** [E devpost] | 5-layer browser pipeline | 43s/18-LLM-calls → **2.6s/0-calls**; "vision is the fallback, not the foundation" | Speed via structure-first | Devpost |
| **PRESENT!** [E github] | AI phone agent answers calls *as you* in your cloned voice, runs OS tasks | **Claude-Code-style Allow/Deny tool gate** — approve each side-effect from a live dashboard | Human-in-the-loop consent on every action | **1st Place Overall + Best ElevenLabs, HackDartmouth 2026** |

> **The pattern jumps out:** the accessibility lane and the browser-agent lane have *converged on the same two ideas you built* — (1) vision/AXTree perception that "sees like a sighted person," and (2) a consent/handoff gate before consequential steps. PRESENT!, Nimbus, Mariner, and PyData all stage the **approval gate** as the climax. You are not behind; you are in the consensus — which means your edge has to be the part *they skip*: **grounding enforced in code + the verifiable negative.**

---

## 2. The demo EFFECTS that win — and the Clarion asset that already does each

Ranked by "how much it wins" × "you already have it."

1. **Make the agent's reasoning VISIBLE on screen.** Three separate vendor demos use the identical tactic: Operator zooms in and reads the **"list of dots"** plan ("found a recipe") [Y OpenAI]; ChatGPT-agent **overlays the chain of thought in text** over the agent's screen [Y OpenAI]; Mariner narrates each step [Y]. → **Clarion already has the HUD + the sources/consent panel + the glass-box trace.** Copy the *visible-thinking* framing: every spoken fact lights up its source node on screen as it's said.
2. **The consent beat as the emotional peak.** Mariner *"asks me if it should proceed to checkout… you're always in control"* [Y]; PRESENT! **Allow/Deny** gate (1st place) [E]; Nimbus **Handoff Points** [E]; PyData *"approve or reject?"* then it continues [Y]. → **Clarion's `interrupt()` consent gate + the `AWAITING YOUR YES` panel state IS this beat** — and you do it harder (per-step, code-enforced, on an irreversible *financial* step).
3. **"Catch the agent lying" LIVE — the single most Clarion-aligned device.** IBM/Tejas Kumar: a deterministic harness inspects the tool history, proves the upvote/login *actually failed*, and on stage *"it stopped lying… step one to solving a problem is admitting you have one."* [Y]. → **This is your grounding invariant performed live.** Stage the moment where a naive agent would confidently say "done / no fee" and Clarion's gate refuses it. Your **verifiable negative** is the productized, voiced version of this — and nobody else demos it.
4. **The "one agent, zero config, hardest sites" stress-test spine.** AccessBrowse runs Wikipedia→Google→Amazon→Zillow back-to-back: *"a live stress test across the web's most complex interfaces. One agent, zero sight-specific configuration"* [Y]. → **This is your generality montage** — and you can claim it honestly because your reasoner is de-hardcoded (proven on usa.gov + weather.gov, zero site-specific code). Their "zero config" is your "zero site-specific topology."
5. **Narration pacing so the user is never left in silence.** Noor made this an explicit design pillar: *"Blind users lose trust if they don't know what's happening… never left in silence"* [E]. → **Clarion's filler + state-narration ("retrieving… verifying… waiting") covers this** — but it's a thing to *show you did on purpose*, per the persona doc ("Name what Clarion is doing").
6. **Live latency / a hard number on screen.** The voice wow-metric in the wild is *"~600ms latency + graceful interruption handling"* [R Millis AI]; winners quantify (TaskPilot's 2.6s, AccessBrowse's "149 automated tests"). → **Clarion's latency meter (`Moss: 6ms` vs greyed `cold RAG: 340ms`) and a "recall-on-safe-behavior" number** are your quantified-restraint slide (Operator-style: 92% confirm / 94% refuse).
7. **Engineering-credibility close.** AccessBrowse ends on *"149 automated tests… infrastructure as code"* [Y]. → **Clarion: "193 tests green, invariant spec catches a silent weakening, proven on two real gov sites."**

---

## 3. The STORY patterns that win

- **"The real pain isn't reading — it's DOING."** Noor's hook, verbatim [E]: describers tell you what's there; the unmet need is *finishing the transactional, high-stakes workflow.* This is **exactly your foundation's task-aware thesis** — and a competitor just proved it wins. Open on it.
- **Highest-stakes hands-free wins the top prize.** ORION (surgical, "without breaking scrub") took the Gemini Live **Grand Prize** over thousands of entries [E]. The lesson: judges reward voice where the stakes are real and the hands are unavailable. Your bill-pay / gov-form on a non-visual user is the same shape — stakes + no visual channel.
- **Empathy/tone as a scored quality.** LetsHelp won on a *"patient, encouraging tone… for users who may feel frustrated or embarrassed"* [E]. Your persona's "competent, not helpless" register is the *opposite but equally deliberate* choice — own it as a designed voice, not an accident.
- **"Trust comes from collaboration, not raw capability."** Nimbus's line [E], echoed by the Reddit consensus: *"frame it as 'this agent handles X so you focus on Y'… people want to feel empowered, not eliminated"* [R]. → Your "you're in command" is this, sharpened for an audience that *resents* the helpless frame.
- **"The Demo IS the Product."** Noor [E]: *"Every architectural decision — from stealth evasion to narration cadence — was optimized to make the mission visceral and functional."* Treat the demo as the deliverable; every Clarion design choice should be legible *in the demo*.

---

## 4. The white space Clarion owns (confirmed, not assumed)

1. **Code-enforced grounding — the entire field skips it.** Every accessibility competitor (Noor, AccessBrowse, Spectra, Orbit) and every browser agent narrates/cites via *system prompt only*; none has a kernel that refuses an ungrounded spoken value [E synthesis]. Clarion's `policy.py` membership gate is the differentiator, and it's invisible to copy in 24h.
2. **The spoken verifiable NEGATIVE.** No surveyed project — winner or entry — demos *"there is no late fee here, verified absent."* It's not in any rubric, any benchmark, any demo. It's yours alone.
3. **Grounding is already a JUDGED criterion.** Gemini Live rubric: *"Does the agent avoid hallucinations? Is there evidence of grounding?"* (part of the 30% Technical/Architecture weight) [E]. You're not pitching a niche virtue — you're maxing a scored axis the others gesture at.
4. **AXTree-first vs the field's vision-grid.** Most a11y winners (AccessBrowse's 1000×1000 grid, Orbit's pixels) went **pure vision**. That's the architecture the research says is "fundamentally ambiguous" and *can't cite a source*. Clarion's merged-AXTree → source-node citation is what makes grounding *possible* — they literally cannot do your negative because their "page" is a screenshot. [Y DOM-vs-vision: 68s vs 225s]

---

## 5. Demo-craft ANTI-patterns (what kills a demo)

- **Flashy autonomy with no Wow = death.** Atlas's launch: *"barely 20 minutes… no 'Wow' moments… buggy and slow"* [R]. A do-everything agent that stumbles live is worse than a narrow one that nails it.
- **Demos that "fall apart in real businesses."** The top r/AI_Agents posts hammer that fully-autonomous demos are *"mostly marketing BS; every successful deployment keeps humans making final calls"* [R]. Your HITL is the credible posture, not a limitation.
- **Browser agents blocked by anti-bot.** The "$20 agent" reality: *"can't access any major commercial site… shut down by anti-bot everywhere that matters"* [R]. → **Do NOT stake a live beat on completing a real checkout on a real commercial site.** Use your self-hosted clone for the payment, real public gov sites up to the wall for the montage (your foundation already says this).
- **Don't out-Jarvis the Jarvises.** You lose a capability race to OpenAI/Google. You win a *trust* contrast. Keep the wow in grounding + consent, not raw automation.

---

## 6. Copy-this-then-beat-it — concrete moves for the Clarion demo

| Copy this winning effect | Source | Do it ONE-better (Clarion) |
|---|---|---|
| Visible chain-of-thought overlay | Operator, ChatGPT-agent [Y] | Don't show the *model's* thoughts (confabulatable) — light up the **source NODE** each spoken fact came from. Provenance > narration. |
| The consent/approval gate as climax | Mariner, PRESENT!, Nimbus [Y,E] | Yours is **code-enforced + on an irreversible financial step + voiced** ("your yes executes it") — not a dashboard click. |
| "Catch the agent lying" live | IBM/Tejas Kumar [Y] | Make it a **head-to-head**: the other architecture confidently says "done/no fee"; Clarion's gate refuses + says the **verifiable negative**. |
| "One agent, zero config, hardest sites" stress test | AccessBrowse [Y] | Yours is **zero site-specific topology** (de-hardcoded, proven on 2 gov sites) — run it live on an unrehearsed 2nd site. |
| Narration pacing, never silent | Noor [E] | Keep it, but **quiet** — crisp grounded facts + a rare decisive abstain (per your persona); don't over-narrate. |
| A hard number on screen | Millis/TaskPilot/AccessBrowse [R,Y] | **Recall-on-safe-behavior** (% ambiguous/ungrounded cases correctly abstained/gated) + the `Moss 6ms` latency meter. |
| "The real pain isn't reading, it's doing" | Noor [E] | Open on it. Then the twist they don't have: *and you can't see to check it — so honesty isn't optional.* |

**The single 20-second beat to build the whole demo around** (synthesizing IBM's "stopped lying" + Noor's "doing" + your verifiable negative + the head-to-head): a blind user on a real bill-pay/gov page asks *"is there a late fee?"* — the generic agent (on tape) confidently says "no, you're all set"; Clarion (live) says *"no late fee on this page — I checked the fee section, the field is absent,"* and the source node lights up. That beat is the IBM device, the Noor story, your white space, and your only survivable live failure mode, all at once.

---

## 7. Your exact event — YC Conversational AI Hackathon (June 6–7, this weekend)
- Host + sponsors: **YC + LiveKit + TrueFoundry + Unsiloed AI (YC F25) + MiniMax** [E Moss/LinkedIn]. Prizes: **YC interview, iPhones, sponsor prizes.**
- Three tracks: **Lead Gen · Support · Co-Pilot** ("ambient agents that listen in and surface live context") [E]. Your foundation already picked **Support** (and Co-Pilot is the ambient-context framing) — consistent.
- **Winners are NOT posted yet** (event concluding now). Re-run Devpost / Moss blog / LinkedIn / X in 1–3 days to see what actually won *this* event, then steal the freshest effects.
- MiniMax is "the only open-source frontier model provider" sponsoring the YC hackathon circuit [E] — your MiniMax-M3 brain is on-sponsor-stack; lean into it on the slide.

---

## Numbers worth verifying
- Gemini Live Agent Challenge: **11,878 participants / 1,536 projects / 151 countries** [E].
- YC Browser-Use Hackathon prize pool **$180K+** (Browser Brawl won) [E].
- DOM agents **68s** vs vision **225s** per complex task [Y PY]; screen readers break **20–30%** on SPAs, **96%** of homepages fail WCAG [Y AccessBrowse].
- Voice wow-metric **~600ms** latency + interruption handling [R Millis].
- TaskPilot **43s/18-calls → 2.6s/0-calls**; TITAN **91%** task completion [E].
- AccessBrowse **149 automated tests** [Y]; Clarion **193 tests** (your status doc).
- $150K MiniMax AI Agent Challenge; $4K 100-Agents Hackathon judged on **"Completeness, Business Viability, Presentation, Creativity"** [R].

## Next moves
- **Build the demo around the §6 single 20-second beat** (head-to-head verifiable negative). It's the IBM "stopped lying" device + Noor's story + your white space.
- **Re-scan in 1–3 days** for the actual YC Conversational AI June-6–7 winners (Devpost/Moss/LinkedIn) and graft the freshest winning effect.
- **Steal Noor's three framings verbatim-adjacent:** "the real pain isn't reading, it's doing," narration-pacing-as-trust, "the demo is the product."
- **Add the recall-on-safe-behavior number** to the panel (already in your backlog) so you have a quantified-restraint slide like Operator's 92%/94%.
- Optional: a 4th leg (X/Twitter) for live hackathon-day chatter once winners drop.

## Sources
### Reddit [R]
- Cartesia Hackathon winner (phoneme speech-coach, accessibility-adjacent) — r/LocalLLaMA 2026-02-17 — https://www.reddit.com/r/LocalLLaMA/comments/1r7j7kb/the_guy_that_won_the_nvidia_hackathon_and_an/
- "I won Cursor Hackathon 26' cloning a $700M app" (Open Yapper, voice-to-text) — r/SideProject 2026-02-23 — https://www.reddit.com/r/SideProject/comments/1rcui28/
- Atlas launch "no Wow moments, buggy/slow" — r/OpenAI 2025-10-21 — https://www.reddit.com/r/OpenAI/comments/1ocp03l/
- "agents in demos fall apart in real businesses; humans make final calls" — r/AI_Agents 2025-06-19 — https://www.reddit.com/r/AI_Agents/comments/1lfc2ic/
- "$20 Agent can't shop/book — blocked by anti-bot" — r/ChatGPT 2025-07-25 — https://www.reddit.com/r/ChatGPT/comments/1m9bv7d/
- GUI-grounding "next step for screen readers" — r/accessibility 2025-02-11 — https://www.reddit.com/r/accessibility/comments/1impis6/
- "partnership not replacement" demo framing — r/AI_Agents 2025-07-02 — https://www.reddit.com/r/AI_Agents/comments/1lppzrb/
- Voice wow-metric ~600ms + interruption (Millis) — r/SideProject — https://www.reddit.com/r/SideProject/comments/1bwwh4u/
- $150K MiniMax AI Agent Challenge — r/ChatGPT 2025-08-12 — https://www.reddit.com/r/ChatGPT/comments/1mny13h/
- 100 Agents Hackathon ("Completeness/Viability/Presentation/Creativity") — r/AI_Agents — https://www.reddit.com/r/AI_Agents/comments/1l87ohk/
### YouTube [Y] (yt-rag; keep MM:SS deep-links)
- AccessBrowse (vision grid, stress-test spine, 149 tests) — Steve Harlow 2026-03-12 — https://youtu.be/1BBzOFUTdKw?t=92
- Operator "list of dots" / "I'm just watching" narration — OpenAI 2025-01-23 — https://youtu.be/gYqs-wUKZsM?t=91
- Mariner "asks before checkout / you're in control" — Google DeepMind 2024-12-11 — https://youtu.be/_uBg6syzXhk?t=91
- ChatGPT-agent overlaid chain-of-thought + confirm-at-last-step — OpenAI 2025-07-17 — https://youtu.be/1jn_RpbPbEc?t=190
- IBM/Tejas Kumar "caught the lie" harness ("it stopped lying") — AI Engineer 2026-05-17 — https://youtu.be/C_GG5g38vLU?t=834
- PyData HITL "approve or reject?" beat — 2025-10-05 — https://youtu.be/vAO7fx2UAWY?t=1016
- DOM vs vision 68s/225s — PY 2026-05-22 — https://youtu.be/WshRCrMbn8M?t=95
### Exa [E]
- Gemini Live Agent Challenge winners (ORION/Moonwalk/Wand; grounding rubric) — cloud.google.com 2026-05-16 — https://cloud.google.com/blog/topics/developers-practitioners/winners-and-highlights-of-the-gemini-live-agent-challenge
- Browser Brawl wins YC Browser-Use Hackathon ($180K) — minimax.io 2026-06-05 — https://www.minimax.io/news/minimax-ycombinator-hackathon-building-the-future-of-web
- Noor (vision-first blind navigator; "real pain isn't reading, it's doing"; narration pacing) — devpost — https://devpost.com/software/noor-bsg6ep
- Orbit (multi-track winner; press-and-speak) — github — https://github.com/Sharanya-Raj/Orbit
- PRESENT! (1st HackDartmouth; Allow/Deny gate) — github — https://github.com/lukietee/Present
- LetsHelp/Auralis (2nd HH26; empathetic tone) — github — https://github.com/ericwei1107/Auralis_LetsHelp_HH26
- Nimbus ("Handoff Points"; trust>capability) — devpost — https://devpost.com/software/zcxv
- TaskPilot ("vision is the fallback") — devpost — https://devpost.com/software/taskpilot
- YC Conversational AI Hackathon (event, sponsors, tracks; winners pending) — https://events.ycombinator.com/conversational-ai-hackathon-2026

## Method notes
- Legs run: A (Reddit/apify-macrocosmos, ~154 threads — thin on award lists as expected, but surfaced demo-craft sentiment + 2 named winners), B (YouTube/yt-rag — existing namespaces `yt_agent_prevention_hitl`/`yt_web_agent_capture`/`yt_self_improving_agents`; NO auto-ingest per opt-in rule; corpus is technique-not-award so it gave demo-craft, not winner lists), C (Exa — the workhorse; 38 pages, named winners + rubric).
- Empty/thin: Reddit site-wide scans defaulted to r/Python+r/bittensor (the scraper needs explicit subreddits); r/Blind returned only old AT-troubleshooting.
- **Biggest gap:** the target event (YC Conversational AI, June 6–7) winners aren't published yet — re-scan in 1–3 days.
</content>
</invoke>
