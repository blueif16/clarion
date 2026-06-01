# Conversational AI Hackathon (YC SF, June 6–7 2026) — research brief
_scope: ~120d recency for techniques, all-time for past-winner patterns • generic AI/dev lens • deep dive • generated 2026-05-30_
_legs: Reddit + Exa(ideas) + Exa(past winners) + WebSearch A/B probe • YouTube leg skipped (no on-topic yt-rag namespace)_

---

## TL;DR (the 3 highest-confidence bets)

1. **The hackathon thesis IS the winning strategy: make retrieval visibly disappear from the latency budget.** Voice models are now cheap+fast (Minimax/Cartesia ~40ms TTS), so the differentiator is *predictive/speculative retrieval* — start the vector query while the user is *still speaking* (on partial STT), pre-fetch likely next-turn topics during TTS playback, serve from a hot cache. This is exactly what the sponsor **Moss** (sub-10ms semantic search) is built for, and what the strongest open repos demonstrate (Salesforce VoiceAgentRAG: 75% cache hit, **316× speedup, 110ms→0.35ms**; primd: retrieval hidden inside STT/TTS phases). `[R][E]`
2. **Winners pick a narrow, nameable vertical and engineer ONE 10-second jaw-drop moment.** Not "support for companies" but "the engineer paged at 3am," "the procurement officer," "the PT patient." Every winning voice project (GibberLink, Voxy, RAG of Fire, Procuro) had a single shareable beat and a quantified proof number. `[E]`
3. **Lead-gen / inbound-receptionist voice agents are the most proven, ROI-positive use case right now; ambient co-pilot is the most open/under-built.** Reddit practitioners report real money on inbound-lead agents (15%→40% conversion, +$15k/mo) but **near-zero genuine ambient "listen-and-surface" co-pilots in the wild** — meaning the Co-Pilot track is the highest-whitespace, highest-wow lane if you can pull it off live. `[R][E]`

---

## Directions with the most signal (ranked)

| Rank | Direction | Why it has signal | Best fit |
|---|---|---|---|
| 1 | **Speculative / "retrieve-while-they-talk" RAG with a visible latency meter** | Directly proves the hackathon thesis; sponsor (Moss) exists to enable it; multiple open repos validate the pattern | All 3 tracks; strongest as the *core engine* |
| 2 | **Dual-agent "Slow Thinker / Fast Talker" split** | Lets you do expensive RAG/reasoning without blocking the turn; measured 75–86% cache hit, 316× speedup | Co-Pilot, Support |
| 3 | **Ambient "agent-as-participant" that joins a live call and PUSHES context** | Whitespace (almost no practitioner reports); high wow; LiveKit makes the agent a call participant | Co-Pilot |
| 4 | **Inbound lead qual → live BANT scoring → warm transfer to human with on-screen briefing** | Highest proven ROI; the AI→human handoff with preserved context is a great demo beat | Lead Gen |
| 5 | **Grounded support bot with a live "sources" panel (citations)** | Trust is the support-bot blocker; showing the retrieval trail wins judges (RAG of Fire pattern) | Support |
| 6 | **Vision + voice multimodality ("see what I see")** | Consistently places in recent hackathons (Pep, Roadmate, IRIS) — a current edge | Support, Co-Pilot |

---

## Product / demo ideas that fit each track

### 🎯 Lead Gen — "agents that nurture & convert inbound leads"
- **"Sub-1s Receptionist + Warm Handoff."** On first ring, look the caller up (Unsiloed-parsed docs + CRM via Moss), run adaptive BANT qualification, score the lead live on screen, then **warm-transfer to a (human or 2nd-agent) AE with an auto-generated briefing card**. Demo beat: the handoff where context is preserved. Proof metric: *"answered in <1s, qualified in 90s, briefing written before the human picks up."*
- **Nurture-sequence agent** that calls a lead back within 2 minutes of an inbound form fill, then orchestrates WhatsApp/email/CRM follow-up — the proven Meta-Ads→Retell→n8n→CRM pattern that Reddit reports took conversions 15%→40%.

### 🛟 Support — "service bots that instantly pull docs + user history"
- **"Zero Dead-Air Support."** Mid-sentence, the bot pulls the user's history + the exact doc passage via Moss (<10ms) and answers with no pause. **Show a side-by-side latency meter: cold RAG 300ms vs cached 5ms**, plus a live "sources" panel proving every answer is grounded (kills the hallucination objection in front of judges).
- **Voice agent that handles a genuinely hard multi-doc question** (e.g., "does my plan cover X given I upgraded in March?") by fusing user-history retrieval + policy-doc retrieval — emulating RAG of Fire's "show the decision trail" trust win.

### 🤝 Co-Pilot — "ambient agents that listen and display live context" (highest whitespace)
- **"Objection Co-Pilot."** Agent silently listens to a live sales/support call and **pushes** (not searches) the right answer/objection-handling card to the rep's screen in <3s. Replicates the Salesforce Enterprise Sales Copilot **14× speedup (25–65s manual → <3s)**. The on-screen knowledge-graph/source traversal is the "wow."
- **"Meeting agent-as-participant"** (à la EchoGuard): the agent *joins* the call as a LiveKit participant, captures decisions/risks/action-items live, and gives a **spoken recap on demand** ("what did we just agree to?"). Ambient + voice-out is rare and demos beautifully.

---

## What past winners do RIGHT (the playbook)

1. **Narrow, nameable vertical + a specific human.** Pain instantly legible to judges. ("procurement officer," "drowsy driver," "engineer paged at 3am.")
2. **Engineer ONE jaw-drop moment.** GibberLink's AI-to-AI sound-protocol switch went viral to Forbes; Voxy spun up 9 voice agents live with zero code. Build everything around that 10-second beat.
3. **Build the demo first, backward from a 30-second script.** One screen, one input, one output, one action. If you can't explain it in a sentence, it's too complex.
4. **Live, judge-proof demo > slides.** Cache/pre-run example inputs, build a demo-mode fallback, use small clean data you control. Reliability is a *deliberate engineering choice*, not luck.
5. **Real data + a number.** "8 min → 45 sec," "$144/mo vs $9,628/mo," "kept a scammer on the line 33 min." Quantified > adjectives.
6. **Voice-specific wow = latency + interruptibility + emotion.** Sub-300–500ms response, barge-in (kill TTS instantly on interrupt), backchanneling ("mhm"), sentiment-matched prosody. Judges reward *human-like flow* over raw voice quality (Presence won despite higher latency by choosing naturalness).
7. **Honest architecture as a selling point.** Zo/EMPIRE pre-empted "how can a 4B model do live video?" by openly explaining clip-stitching — turned a limitation into a credibility win.
8. **Ruthless pitch iteration + reserved polish time.** RAG of Fire rewrote its whole pitch (same demo) after an outsider said "I'm lost." Reserve the final 3–4 hrs purely for polish + rehearsal — no new features.
9. **Talk to sponsors early — they're often the judges.** A voice hackathon wants voice; build to the brief, and ask the sponsor desk what they want to see (Eyal Shechtman, 5× winner).
10. **Have a dedicated storyteller on the team.** Builder + product + pitch roles; many winners credit a non-coder PM/pitch lead who reframed the narrative.

### Named projects worth emulating
- **Voxy** (grand prize) — zero-code voice-agent generator; *won by building a live agent on stage from a company name.* Maps to all 3 tracks.
- **GibberLink** (ElevenLabs global winner) — *won purely on one shareable "wait, what?" moment.*
- **RAG of Fire** (Klaviyo grand prize) — *picked a universally-felt pain ("3am page") and showed citations so judges trust the AI* → model for the Support track.
- **Procuro / Dealwise** (ElevenLabs) — *outbound voice agents that actually phone real suppliers live* → Lead-Gen template.
- **EchoGuard** (LiveKit) — *agent-as-participant that joins a call and gives spoken recaps* → Co-Pilot blueprint.
- **Zo/EMPIRE** — *won credibility via brutal unit economics + radically honest architecture.*

---

## Numbers worth verifying / citing on stage
- **Latency budget (LiveKit target):** total **<800ms**; STT <200ms, LLM TTFT <300ms, TTS TTFB <300ms. `<500ms feels natural.` `[E]`
- **Per-layer production budget:** STT 80–120ms, LLM 150–250ms, TTS 60–100ms, network 20–60ms. `[E]`
- **Salesforce VoiceAgentRAG:** 75% cache hit, **316× speedup (110ms → 0.35ms).** `[E]`
- **Enterprise Sales Copilot:** answer on rep dashboard in <3s = **14× vs 25–65s manual.** `[E]`
- **Moss (sponsor):** claims **sub-10ms** retrieval; "retrieval disappears from the latency budget." *(repo found; sub-10ms claim not independently benchmarked — verify at the sponsor desk.)* `[E]`
- **Qwen3-TTS:** ~97ms streaming TTS, 3s voice clone — open ElevenLabs alternative. `[R]`
- **Reddit ROI anecdote:** inbound-lead voice agent took conversions 15%→40%, +$15k/mo. `[R]`

---

## Technique cheat-sheet (build notes)
- **Hide retrieval inside the speech phases** — fire the vector query on partial STT (user still talking) and pre-fetch likely next topics during TTS. Serve from hot cache → retrieval falls off the budget.
- **Decouple Slow Thinker (background context predictor) from Fast Talker (foreground responder).**
- **Turn-taking is the highest-leverage UX lever.** Use model-based semantic turn detection (LiveKit MultilingualModel) over fixed silence thresholds; keep turn detection live *during* TTS for barge-in; `resume_false_interruption` to ignore background noise.
- **LiveKit knobs:** `preemptive_generation=True` (LLM drafts on partial transcript), prewarm Silero VAD, co-locate agent with models, cap `max_tool_steps`, play a "thinking" sound during tool calls.
- **Memory loop:** retrieve-before-LLM + **async-write-after** (never block the response); domain-specific extraction prompts beat generic capture (Mem0, MongoDB `$rankFusion`). LiveKit + MongoDB hacker starter has 5 ready patterns.
- **Stream at every layer; use WebRTC (UDP), not HTTP/WS** — persistent transport kills per-step 100–300ms round trips.
- **Canonical stack practitioners name:** STT (Deepgram) → LLM → TTS (ElevenLabs / Cartesia Sonic-3 / Minimax / open Qwen3-TTS), glued by **LiveKit or Pipecat**; memory via Mem0/Zep; search via Moss/Exa. OSS path: **Dograh** (Vapi/Retell alternative).

---

## What's broken / contested (don't get burned)
- **RAG-for-voice latency is THE unsolved bottleneck** in the wild — Reddit practitioners report 300ms–2s retrieval (local FAISS+MiniLM ~300–400ms; Pinecone ~2s) blowing the conversational budget. *This is precisely the gap the hackathon (and Moss) want you to close — lean into it, don't hide it.*
- **Hype vs reality:** glossy "$15k/mo voice agent" posts sit next to a 40-project builder warning that most sold "agents" are a $4k automation + one LLM call, over-scoped ~7×. Judges who've seen 100 demos smell over-claiming — be honest (pattern #7).
- **Ambient co-pilot is genuinely early-stage** — almost no practitioner reports. High risk *and* high reward; only attempt if you can make it work live.
- **Closed vs open voice stack** churn: heavy ElevenLabs/Vapi/Retell reliance but real momentum (and some discontent) toward OSS (Dograh, Pipecat, Qwen3-TTS).

---

## Next moves
1. **Pick the engine, then the track.** Build the speculative-retrieval + dual-agent core first (it's the thesis and the reusable wow), then wrap it in whichever track gives the cleanest 30-second story. Default recommendation: **Co-Pilot** (highest whitespace) or **Support with a live latency+sources meter** (cleanest proof of the thesis).
2. **Prototype the one wow moment in the first 4 hours** — the visible "cold 300ms vs cached 5ms" latency meter or the AI→human warm handoff card. Everything else serves it.
3. **Hit the sponsor desks Saturday afternoon** (Moss, LiveKit, Minimax, Unsiloed): confirm Moss's real retrieval latency + API, grab credits, and ask each "what would make you pick a project?" — they judge.
4. **Reserve Sunday 7:30–11:00 AM for polish + pitch rehearsal only.** Write the 2-min script, build the demo-mode fallback, rehearse the barge-in/interrupt beat so it never fails on stage.
5. **Follow-up research if needed:** sponsor-specific patterns (TrueFoundry deploy latency, Minimax voice cost/quality, Unsiloed PDF→embeddings pipeline) — these were the main gap across legs.

---

## Sources

### Reddit `[R]`
- AI phone receptionist, <2s, callers can't tell — https://www.reddit.com/r/AI_Agents/comments/1ram5jo/i_set_up_an_ai_phone_receptionist_for_my_friends/
- "Stop building agents" — most $30k agents are $4k automation + 1 LLM call — https://www.reddit.com/r/AI_Agents/comments/1taei9m/stop_building_ai_agents/
- Near-realtime RAG for LiveKit (FAISS 300–400ms, Pinecone ~2s) — https://www.reddit.com/r/LangChain/comments/1lbb54b/how_to_do_near_realtime_rag/
- Multi-voice-agent practitioner (Dograh) — https://www.reddit.com/r/LangChain/comments/1rsm67q/i_think_im_getting_addicted_to_building_voice/
- Lead-gen: Meta Ads→Retell→n8n→CRM, 15%→40%, +$15k/mo — https://www.reddit.com/r/AI_Agents/comments/1nkkjuj/how_a_2000_ai_voice_agent_automation_turned_a/
- Qwen3-TTS ~97ms open TTS — https://www.reddit.com/r/LocalLLaMA/comments/1qlzbhh/release_qwen3tts_ultralow_latency_97ms_voice/
- Voice-to-voice RAG POC (OpenAI Realtime + LangChain + Qdrant) — https://www.reddit.com/r/LangChain/comments/1hm1e1m/open_ai_realtime_with_langchain_powered_rag_poc/
- Agent-native infra (AgentPhone/ElevenLabs/Mem0/Exa) — https://www.reddit.com/r/artificial/comments/1sdiugx/you_can_now_give_an_ai_agent_its_own_email_phone/

### Exa `[E]`
- Salesforce VoiceAgentRAG (dual-agent, 316× speedup) — https://github.com/SalesforceAIResearch/VoiceAgentRAG
- LiveKit latency playbook — https://livekit.com/blog/understand-and-improve-agent-latency
- Production voice AI latency architecture (DEV.to) — https://dev.to/dishant_sethi/building-production-voice-ai-agents-latency-architecture-and-what-nobody-tells-you-3jhj
- Salesforce Enterprise Sales Copilot (14× speedup) — https://github.com/SalesforceAIResearch/enterprise-sales-copilot
- **Moss** (sponsor) real-time semantic search runtime — https://github.com/ankitmukherjee101/moss
- LiveKit turn-detection guide (VAD/endpointing/model-based) — https://livekit.com/blog/turn-detection-voice-agents-vad-endpointing-model-based-detection
- primd (10MB Rust retrieval-hiding runtime) — https://github.com/rohansx/primd
- Mem0 voice-memory loop — https://mem0.ai/blog/ai-memory-for-voice-agents
- LiveKit + MongoDB hacker starter (5 patterns) — https://github.com/livekit-examples/mongodb-hacker-starter
- **Past winners:** ElevenLabs Worldwide Hackathon (GibberLink, Procuro, Dealwise…) — https://elevenlabs.io/blog/announcing-the-winners-of-the-elevenlabs-worldwide-hackathon
- AssemblyAI SF voice hackathon (Voxy) — https://www.assemblyai.com/blog/voice-agent-hackathon-sept-19
- AssemblyAI "7 voice projects" (Wynnie, AI Debate, Hogwarts) — https://www.assemblyai.com/blog/these-7-voice-ai-projects-just-blew-us-away
- "How to win an AI hackathon" (RAG of Fire) — https://klaviyo.tech/how-to-win-an-ai-hackathon-build-a-solution-that-actually-matters-aab49307587e
- Microsoft Reactor winner write-up (Presence) — https://jayminwest.com/blog/6-winning-microsoft-reactor-ai-hackathon
- EchoGuard (LiveKit agent-as-participant) — https://www.linkedin.com/posts/dwiteekrishnapanda_londonaihack-livekit
- Zo/EMPIRE (honest architecture) — https://github.com/adityasingh2400/Zo

### WebSearch (A/B probe)
- livekit/agents (canonical OSS) — https://github.com/livekit/agents
- LiveKit AI Voice Agents 2026 Playbook — https://www.forasoft.com/blog/article/livekit-ai-agents-guide
- "Lessons from implementing RAG in a real-time voice agent" — https://medium.com/@jorge.jarne/lessons-from-implementing-rag-in-a-real-time-voice-agent-livekit-43f0689bf565
- Devpost — how 5 judges score hackathons — https://info.devpost.com/blog/hackathon-judging-tips

## Method notes
- Legs run: **Reddit + Exa(ideas) + Exa(past-winners) + WebSearch A/B probe.** YouTube/yt-rag leg **skipped** — the local corpus covers only motion-design + algo-trading; no namespace fits conversational/voice AI. (Offer: ingest a LiveKit/YC/voice-AI channel and re-run.)
- **Exa vs WebSearch A/B:** Strong overlap on AssemblyAI winner recaps and LiveKit primary sources; Exa uniquely surfaced the **Moss sponsor repo**, Salesforce VoiceAgentRAG/Enterprise-Copilot, primd, and the dual-agent pattern (the load-bearing technical signal). WebSearch leaned slightly more SEO-blog on the "how to win" query. Net: **Exa won the technical depth; WebSearch confirmed the canonical sources.**
- Echo-chamber check: AssemblyAI/ElevenLabs recaps appear across multiple legs (expected for winner data); technical claims corroborated across ≥2 independent repos.
