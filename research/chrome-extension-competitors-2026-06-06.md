# Chrome-extension competitive landscape — Clarion market scan
_scope: shipped/commercial products + the closest demo repos • blind/low-vision voice-copilot lens • generated 2026-06-06_
_legs: Exa (Web Store w/ live install counts · GitHub · company) + WebSearch • install counts pulled from chromewebstore.google.com on 2026-06-06_

> Purpose: fill the gap our two prior briefs left. `research/conversational-ai-hackathon-2026-05-30.md`
> covers winning *patterns*; `research/clarion-execution-architecture-2026-05-31.md` covers *how to build*.
> Neither mapped the **products on the market**. This one does — and the headline is a positioning gift.

---

## TL;DR (highest-confidence)

1. **The exact niche — a blind-first voice co-pilot *Chrome extension* — has NO product with traction.**
   Every extension in Clarion's precise lane has near-zero installs (Phantom **6**, YourVoice **28**,
   EchoLog **48**, CROMI **25**). A dozen near-identical clones shipped Feb–Mar 2026 for the *Gemini
   Live Agent Challenge*; all are GitHub demos. Shipping isn't the moat — **trust + a live demo is.**
2. **Soft consent + source citation are now table-stakes among the agentic browsers** (Comet cites
   sources; Comet/Atlas/Neon/Chrome-Auto-Browse all "ask before sensitive steps"). Clarion's edge is
   that these are a **hard kernel invariant** (*no fact without a source, incl. negatives; no action
   without a yes; no memory without a yes*) — not a heuristic. Nobody enforces it.
3. **The real competition isn't other extensions.** Blind/low-vision spend sits in **apps/services**
   (Be My Eyes ~1M, Aira) and the new **agentic browsers** (Comet/Atlas/Neon/Dia). Clarion's wedge is
   the intersection neither covers: *blind power-user, in-browser, grounded-and-consented, private
   high-stakes tasks.*
4. **AXTree-first is now a genuine differentiator, not just a build choice.** Almost every rival is
   vision/coordinate-first (screenshot → Gemini Computer Use → click x,y) — which **structurally cannot
   cite what it read**. Only **Noor** does AXTree-primary; **Spectra** is hybrid. Both are demos.

---

## Tier A — Direct competitors: blind/low-vision voice navigators (the exact lane)

Almost all are Feb–Mar 2026 *Gemini Live Agent Challenge* repos. **Vision/coordinate-first unless noted.**

| Product | Status / traction | Perception | Why it matters |
|---|---|---|---|
| **Noor** (`aliahmedd24/noor`) | GitHub repo | **AXTree-first** (<1s) + vision fallback (~30s) | **Closest to Clarion's thesis**: narrates every action, guardrails = "no fabrication, uncertainty disclosure." Gemini 3.1 Pro + Live, Playwright. No users. |
| **Spectra** (Aqta Technologies, Dublin) | repo (~2★), v2.0.0, site spectra.aqta.ai | **Hybrid** — `read_page_structure` + vision | Has a `confirm_action` tool (soft consent). Gemini Live, MV3 "Spectra Bridge" extension. |
| **Phantom** (`youneslaaroussi`) | **Web Store — 6 installs**, open source | Vision (Gemini 3 Flash computer-use) + AX snapshot | Most-polished shipped one; 20 tools, privacy shield (blurs PII pre-screenshot), personas. General-purpose, markets blind/hands-free. |
| **AccessBrowse** (`sgharlow`) | repo (0★) | Vision, coordinate (1000×1000) | "Coordinate-based browsing… works on any site without config" — the anti-AXTree bet. |
| **Sally** (`Manoj7ar`) | repo (27★) | Vision + DOM | macOS Electron, motor-impairment focus, 40-step/10-min safety cap. |
| **IAN** (`azaynul10`) | repo (0★) | Vision, headless Playwright | Dual-model (audio orchestrator + visual navigator). |
| **ReSight** (`vietnguyen2358`) | repo | Stagehand (DOM via Gemini) | "Split-brain" agent council; **Guardian** does dark-pattern detection + purchase confirmation. ElevenLabs voice. |
| AccessBot · VoxSight · WayPoint · Dom · ScreenSense · GozAI · Visio | repos | mostly vision; WayPoint/Dom index DOM | Same idea, same cycle. Dom uses **numbered element overlays** ("click 5") — same trick as Clarion's AXTree index. |

## Tier B — Voice-control accessibility extensions (command-based / light AI)

| Product | Installs | Note |
|---|---|---|
| **LipSurf** | **9,000** | Category leader; general/multitasking, premium wake-word. Not blind-specific. |
| **Handsfree for Web** | 2,000 | Hundreds of mouse/keyboard voice commands. |
| Contextli (ex-AudioAI) | 940 | Dictation into AI sites. |
| Click by Voice | 905 | Keyboard-driven element activation. |
| Voice Everything | 146 | Dictation + summarize, 25+ langs. |
| Web Assist | 103 | Navigation + dictation. |
| Genie 007 | 49 | "AI voice assistant for any site." |
| EchoLog AI | 48 | Hands-free nav + page description. |
| Mind Cursor | 36 | Face-gesture cursor + Gemini voice. |
| VoicePilot | 30 | 35+ commands, tab/nav/bookmarks. |
| **YourVoice** | **28** | **Explicitly blind-focused** (AI+NLP, Malay/English) — the truest niche match, 28 users. |
| CROMI | 25 | Voice browser control. |

## Tier C — General AI web-agent extensions (NOT accessibility — where the traction is)

| Product | Installs | Note |
|---|---|---|
| **Claude (Anthropic)** | **8,000,000** | In-browser agent: navigates, fills forms, multi-step workflows. |
| **Merlin AI** | 1,000,000 | 26-in-1 research/rewrite/summarize. |
| **HARPA AI** | 400,000 | Multi-model page-aware sidebar + automation. |
| **Nanobrowser** | 50,000 | Open-source, **local**, BYO-key, multi-agent. |
| Retriever / Page Agent Ext | 20,000 each | Self-driving web agents (scrape/fill/monitor). |
| Side Copilot | 9,000 | Arc-style sidebar agent. |
| Agentic Browser | 1,000 | NL automation. |
| **idoit** | **117** | Markets "10,000+ users" — Web Store shows 117. BYO-key, "asks permission before risky actions." |
| BrowserGPT (CIVAI) | 78 | Voice-activated, live tab agent. |

## Tier D — The real incumbents for blind users (NOT extensions)

- **Be My Eyes / Be My AI** — ~1M users. **Windows desktop app** ("navigate inaccessible documents,
  dashboards, **websites**, business software") + hands-free on **Ray-Ban/Oakley Meta glasses**
  (announced CSUN, Mar 2026). GPT-vision based.
- **Aira** — human visual interpreters + **Access AI** + **Project Astra** real-time video (Trusted
  Tester); now on Meta glasses. Human-takeover when AI hits limits.
- **SpeakyAI** — mobile "conversational screen reader," includes "internet voice browsing."
- **JAWS + Picture Smart AI + FS Companion** — Windows, AI image description + in-app navigation (paid).
- **ChromeVox** — Google's built-in Chrome screen reader (the incumbent our user already drives).

## Tier E — Agentic browsers (full browsers, not extensions — the 800-lb gorilla)

| Browser | Vendor | Consent / citation posture |
|---|---|---|
| **Comet** | Perplexity | Free, Chromium, **cites sources**, sidecar, **asks confirmation before acting**, on Android. |
| **ChatGPT Atlas** | OpenAI | Agent Mode (paid), browser memory, macOS; "requires more confirmation steps." |
| **Opera Neon** | Opera | $19.90/mo, **Neon Do runs locally**, visible actions, **pause/take-control anytime**, MCP endpoint. |
| **Dia** | Atlassian / Browser Co. ($610M) | Skills, work-app integrations; assist-leaning, not full-auto. |
| **Chrome + Gemini "Auto Browse"** | Google | Jan 2026; multi-step tasks; **"for sensitive steps like purchases, it asks the user to approve."** |
| **Brave Leo** | Brave | Privacy-first, local models (Ollama). |

## Tier F — Site-side / B2B "make the site agent-ready" (the opposite architecture)

- **AccessioAI** — site embed; makes a site "agent-discoverable," screen-reader control via tool calls,
  "AI suggests fixes — **every change requires human review**" (explicit anti-overlay stance).
- **ANVE.AI / AnveVoice** — "Agentic Voice OS," **site-side** (gov/edu/health portals), ~1,000 signups,
  B2B. The structural opposite of Clarion's user-owned model (cf. `foundation.md`'s accessiBe rebuttal).

---

## What this means for Clarion's positioning

1. **No incumbent owns the lane** — but a dozen teams tried this exact thing 3 months ago and got zero
   users. The differentiator is enforced trust + a working live demo, not the feature list.
2. **Consent + citation are commoditizing as soft heuristics.** Clarion must *show* them as a hard
   invariant on stage (the grounded readback, the per-step "yes", the honest "I can't find that",
   the irreversible hard-stop) — the thing Comet/Atlas/Neon gesture at but don't guarantee.
3. **Lead the comparison against Be My Eyes/Aira (vision/human, app-based) and Comet/Atlas (agentic
   browsers), not against other extensions.** The extensions are demos; the apps/browsers are the real
   alternatives a blind power-user weighs.
4. **AXTree-first is a moat narrative**: vision/coordinate clicking can't cite its source — so it
   *cannot* satisfy "no fact without a source." Only Noor does AXTree-primary, and it's a demo.

---

## Numbers worth citing on stage (verify before quoting)
- Closest niche extensions, live Web Store installs (2026-06-06): Phantom **6**, YourVoice **28**,
  CROMI **25**, EchoLog **48**, VoicePilot **30**. Voice-control leader LipSurf **9,000** (general).
- General AI web-agent extensions for contrast: Claude **8M**, Merlin **1M**, HARPA **400K**.
- Be My Eyes ~**1M** users; on Windows desktop + Meta glasses (CSUN Mar 2026).
- Chrome's own "Auto Browse" (Gemini 3, Jan 2026) **asks approval for sensitive steps** — i.e. soft
  consent is now a default-browser feature.

## Sources
- AI browser landscape 2026 — https://www.digitalapplied.com/blog/ai-browser-landscape-2026-atlas-comet-arc-dia
- Atlas vs Neon vs Comet vs Dia — https://o-mega.ai/articles/agentic-browsers-in-2025-atlas-neon-comet-dia-full-comparison
- Comet launch — https://www.perplexity.ai/hub/blog/introducing-comet
- Opera Neon ships — https://blogs.opera.com/news/2025/09/opera-neon-agentic-ai-browser-release/
- What is an AI browser (Atlas/Comet/Chrome Auto Browse) — https://getaibriefs.com/blog/what-is-an-ai-browser-explained/
- Be My AI — https://www.bemyeyes.com/bme-ai/ · Be My Eyes for Windows — https://www.bemyeyes.com/be-my-eyes-for-windows/
- Be My Eyes + Meta glasses (Mar 2026) — https://www.bemyeyes.com/business/news/be-my-eyes-and-meta-launch-new-accessibility-functions/
- Aira AI / Project Astra — https://aira.io/ai/
- SpeakyAI — https://speakyai.com/
- Noor (AXTree-first) — https://github.com/aliahmedd24/noor
- Spectra (hybrid + confirm_action) — https://github.com/Aqta-ai/spectra
- Phantom (Web Store) — https://chromewebstore.google.com/detail/phantom/pfhlohjaccmfjocncjieckpphcamfeom
- LipSurf — https://chromewebstore.google.com/detail/lipsurf-voice-control-for/lnnmjmalakahagblkkcnjkoaihlfglon
- YourVoice — https://chromewebstore.google.com/detail/yourvoice-control-browser/fefajchpiabpmnafcejehefafkdlgeej
- idoit — https://chromewebstore.google.com/detail/idoit-ai-browser-agent-wo/meghnjamhhnfjgbclienkdlanhkjbija
- AccessioAI — https://accessio.ai/ · ANVE.AI — https://linkedin.com/company/anve-ai

## Method notes
- Install counts are the load-bearing finding: they reclassify "competitors" (demos, single-digit
  installs) vs "real alternatives" (apps/services + agentic browsers). Counts are a point-in-time
  Web Store read (2026-06-06) and drift; re-pull before quoting.
- yt-rag leg skipped (no on-topic namespace). Echo-chamber risk low: GitHub/Web-Store/company/news
  are independent source classes.
