# Validating confident LLM choices + RAG groundedness — research brief
_scope: 2025–2026 (current best practice), generic LLM-engineering lens, deep dive • generated 2026-06-06_
_source tags: **[R]**=Reddit • **[Y]**=YouTube (yt-rag) • **[E]**=Exa web. Inline citations name the specific source so every claim is traceable._
_three legs ran in parallel (Reddit · YouTube RAG · Exa). The YouTube corpus was thin/off-topic for this query (see Method notes) — weight Reddit + Exa for the concrete methods._

## How to read this
Two questions, each with the same shape of answer: **don't trust the generator's own say-so; attach an external, calibrated check, and abstain when it fails.** Claims are tagged practitioner-experience **[R]/[Y]** vs. benchmarked/papers/vendor **[E]**. The last two sections map everything onto Clarion's invariant and give copy-paste scaffolds.

## TL;DR
1. **Self-grading is a trap. Separate the judge from the generator.** Cross-source consensus: "intrinsic self-correction is unreliable… prompting an LLM to 'check your work' without external grounding degrades reasoning" [E zylos.ai]; "self-evaluation, very much a trap — just use an adversarial evaluator" [Y AI Engineer/Anthropic workshop]; use a *separate* binary auditor, not the policy [Y AI Agent Frontier/Huang]. Self-correction only helps when grounded in **external feedback** (tests, retrieval verification, tool-output comparison) [E zylos.ai].
2. **Confidence = fuse multiple signals, then calibrate, then abstain.** The 2025–2026 frontier (UniCR) fuses **sequence-likelihood + self-consistency dispersion + retrieval-compatibility + verifier feedback** into a calibrated `P(correct)`, then refuses under a **conformal risk budget** [E arxiv 2509.01455]. No single signal is enough.
3. **For RAG, the load-bearing method is claim-level NLI grounding.** Decompose the answer into atomic claims → check each against retrieved context with an NLI/entailment model → **faithfulness = supported claims / total claims**; the fraction with *no* supporting chunk is your hallucination rate [E RAGAS, FutureAGI, SelfCheckGPT-NLI, HALT-RAG]. Answer-level "is it grounded?" scoring "is a vibe check" [E FutureAGI].
4. **Factuality ≠ faithfulness — and this is exactly Clarion's axis.** A claim can be *true* but *unsupported by the page*; conflating them mislabels things and, worse, "models become **more confident** generating statements that appear in the retrieval, regardless of factual correctness" [E FRANQ 2505.21072]. Clarion already chose the right axis: speak only what's *sourced on the page*, not what's *probably true*.
5. **Verification has a latency tax — pay it in tiers.** Naively stacking checks turned a 1.4s eval into **3.8s median / >9s p95** [E tianpan.co]. Fix: Tier 0 deterministic (ms) → Tier 1 small classifier (20–60ms) → Tier 2 heavyweight LLM judge **only on the uncertain slice**; run input checks in parallel and **stream output checks alongside generation**.

---

## Part 1 — Validating a *choice* is made confidently

### The signal families (and when each is worth it)
| Signal | What it is | Cost/latency | Notes |
|---|---|---|---|
| **Verbalized confidence** | ask the model "how sure are you (0–1)?" / elicit + abstain | ~free (prompt-only) | Stable under paraphrase, reasonably calibrated vs token-prob; *advanced* VC instructions add little and can hurt math [E VCSC openreview 66D3rZrNjV; I-CALM 2604.03904] |
| **Token-logprob / sequence-likelihood** | read the probability of the chosen token/answer | ~free if API exposes logprobs | "One-token trick": force a single-token answer, read its logprob to score/route cheaply [R LLMDevs 1k0nfnv]. UniCR uses seq-likelihood as one fused feature [E] |
| **Self-consistency / sampling dispersion** | sample N times, measure semantic disagreement | N× generation | The backbone of SelfCheckGPT & semantic-entropy detectors [E; R MachineLearning 1iu9ryi]. **CISC**: confidence-weighted majority vote cuts required samples **>40%** [E 2502.06233] |
| **Ensemble / LLM-as-judge voting** | a *separate* model (or panel) scores the candidate | 1+ extra call | Now load-bearing in prod; small distilled judges (Luna-2 3–8B, Prometheus-2 7B, Patronus Lynx 8B) hit **0.88–0.95 acc at ~97% lower cost** [E zylos.ai] |
| **Retrieval/tool compatibility** | does the choice agree with retrieved evidence / tool output? | depends | A top driver of abstention in UniCR ("evidence contradiction, semantic dispersion, tool inconsistency") [E 2509.01455] |
| **Calibration + conformal abstention** | map raw scores → true probability; refuse under an error budget | tiny head | Temperature scaling + proper scoring; **conformal risk control** gives distribution-free guarantees valid under shift [E UniCR] |

### Best-practice synthesis (what teams actually do, 2026)
- **Fuse, don't pick.** UniCR's "evidence fusion → calibrated probability → risk-controlled decision" is the state of the art: combine seq-likelihood + dispersion + retrieval-compatibility + verifier into a calibrated `P(correct)`, threshold by a user error budget [E arxiv 2509.01455]. It works black-box (API-only features).
- **Calibrate *within* a question, not across.** CISC's finding: standard cross-question calibration (ECE) is a *poor* predictor of which answer to the *same* question is right — "the most calibrated confidence method proved least effective for CISC." Use within-question confidence to weight votes [E 2502.06233].
- **Two samples already help a lot.** VCSC: verbalized-confidence + self-consistency hybrid gets "dramatic gains with just two repeats" — you don't need N=20 [E openreview 66D3rZrNjV].
- **Make abstention a first-class action with a reward.** I-CALM (prompt-only: verbal confidence + announced abstention reward + humility norms) traces an **abstention–hallucination frontier** — you *tune* coverage vs. reliability, no retraining [E 2604.03904]. HALO-Loss does the training-time version ("teach networks to say I don't know") [R MachineLearning 1skzuhd].
- **For agent *action* selection specifically:** the corpus is thinner here, but the transferable patterns are (a) entropy-driven branching — "branch more where next-token entropy is high," progress = entropy reduction toward the answer [Y AI Agent Frontier/Huang]; (b) an adversarial evaluator that **actually executes** the action in a sandbox (Playwright) and critiques the *output*, not the generator's reasoning trace [Y AI Engineer]; (c) verifiable-reward gating (unit tests / checkers / simulators returning pass/fail) where a verifier exists [Y Huang, RLVR].
- **Gap flagged honestly:** no clean, standalone recipe surfaced for *logprob/sequence-probability over a set of candidate actions* (most logprob material is QA/long-form). Self-consistency dispersion + retrieval-compatibility are the better-supported signals for "which option to click."

---

## Part 2 — Confirming a RAG answer is grounded / not hallucinated

### The core pattern (cross-source consensus)
**Claim-level decomposition → per-claim grounding check (NLI) → contradiction scan → report per-claim rate → abstain on unsupported.** [E RAGAS, FutureAGI, SelfCheckGPT, HALT-RAG]

FutureAGI's deep dive is the most actionable: answer-level groundedness hides three failure modes (cherry-picking, sycophantic restatement, claim-vs-sentence granularity gap), so the *only* eval that catches what a human audit catches is:
1. **Extract atomic claims** (one fact each, standalone, declarative).
2. **`check_claim_supported(claim) → (supported, score, best_supporting_context)`** — NLI for the cheap path, LLM judge for borderline.
3. **Report per-claim hallucination rate** = fraction of claims with no supporting chunk (not the mean response score).
4. **Contradiction scan over the chunks the model did *not* use** — catches confident statements that the rest of the evidence refutes.
[E https://futureagi.com/blog/evaluating-rag-faithfulness-deep-dive-2026/]

### The methods & tools (named, with specifics)
- **RAGAS Faithfulness** — `(# claims supported by context) / (# total claims)`, via a 2-call pipeline: statement-extraction prompt then an NLI-verdict prompt (verdict 0/1 + reason per claim). Reference-free (no ground truth needed). De-facto production harness alongside **DeepEval** and **TruLens** [E github explodinggradients/ragas; R LLMDevs 1j6pxv9/1i6r1h9].
- **SelfCheckGPT-NLI** — zero-resource, black-box: sample N stochastic generations, score each sentence's **Prob(contradiction)** vs. the samples using DeBERTa-v3-large fine-tuned on MultiNLI. High = likely hallucinated. NLI variant is the recommended one (vs BERTScore/QA/n-gram) [E github potsawee/selfcheckgpt; R MachineLearning 1iu9ryi].
- **Vectara HHEM (Factual Consistency Score)** — a dedicated factual-consistency *model* (not an LLM judge) scoring summary-vs-source **0.0–1.0** (calibrated: "0.95 ⇒ ~95% likely hallucination-free"). HHEM-2.1-Open is T5-based, open weights, short-context; HHEM-2.3 commercial, longer context. Beats LLM-judge by **~10pp**; ~75.8% on SummaC. Live leaderboard (May 2026): top model 1.8% hallucination; for reference claude-haiku-4-5 9.8%, claude-sonnet-4-6 10.6%, claude-opus-4-5 10.9% [E docs.vectara.com; github vectara/hallucination-leaderboard].
- **HALT-RAG** — post-hoc verifier fusing an **ensemble of two frozen NLI models + lexical signals** into a small calibrated meta-classifier (5-fold OOF to prevent leakage) → abstention. HaluEval F1: QA 0.979, summarization 0.776, dialogue 0.739 [E arxiv 2509.07475].
- **LongTracer** — local hybrid STS+NLI claim-grounding at inference, MIT-licensed, **no LLM-judge/API cost** [E dev.to muzammil_endevsols].
- **Reverse RAG** (Mayo Clinic) — verify each *generated* claim back against the retrieved source before surfacing it (claim→source attribution) [R LLMDevs 1j9yk4e].
- **Latent-space probes** — wisent-guard catches hallucinations from activation patterns alone, but only **43%** on unseen TruthfulQA — not production-safe alone [R LocalLLaMA 1jqawj1].

### Critical nuances (the traps)
- **Factuality vs. faithfulness must be kept separate.** FRANQ applies different UQ depending on whether a statement is faithful-to-context, and warns that conflating them "incorrectly labels factually correct statements as hallucinations if not explicitly supported." Crucially: **retrieval presence inflates confidence regardless of correctness** [E arxiv 2505.21072]. → For Clarion, *faithfulness to the page* is the correct target, and we must not let "it sounds right" raise confidence.
- **There's a hard ceiling.** Even pro-grade legal RAG is right only **~65% at best** (Stanford) — treat groundedness/abstention as unsolved and design for refusal, not perfection [R LocalLLaMA 1f61oxc].
- **Post-hoc > prevention.** "Checking claims after generation is cheaper, more reliable, and composes better than any prevention-only strategy" [E tianpan.co]. Failure taxonomy to verify against: **Fabrication** (existence check vs. authoritative source), **Contradiction** (NLI excels — entailed/neutral/contradicted), **Outdated** (subtlest) [E tianpan.co 2026-04-10].
- **Retrieval-side engineering is still the biggest lever in practice** [R]: the most-upvoted "I killed RAG hallucinations" stack = Docling parse → hybrid dense(e5)+sparse(BM25) → **bge-reranker-v2-m3 top-50→5 ("cut wrong-context answers ~60%")** → strict "answer only from context, else abstain" prompt → RAGAS scoring [R Rag 1pxe9jg]. Better retrieval means fewer claims to refuse.

---

## Latency / cost — the refusal-latency tax & the tiered fix
[E tianpan.co/blog/2026-04-28]
- Stacked guardrails: 1.4s → **3.8s median / >9s p95**. Per-layer: prompt-injection classifier 80ms (local DeBERTa)/250ms (API); PII/topic 50–150ms; output moderation 100–300ms; refusal-retry path ≈ **3× cost & latency** on 5–15% of traffic.
- **Tiered architecture (recommended):**
  - **Tier 0** — deterministic regex/denylist, single-digit ms, clears most traffic.
  - **Tier 1** — one small classifier (~0.4B DeBERTa, 20–60ms) for routing + refusal prediction.
  - **Tier 2** — heavyweight LLM judge **only on Tier-1 "uncertain."** Rule of thumb: "if Tier 2 fires on >5% of traffic or guardrails add >80ms to p95, the pipeline is over-engineered."
- **Run input checks in parallel** (−60–70% input overhead); **stream output checks alongside generation** (scan partial output, cut mid-stream on flag) — moves safety cost "from added-to-the-end to hidden-in-the-middle."

---

## Practice → source quick-reference
| Practice | Why it works | Source | Leg |
|---|---|---|---|
| Separate judge/auditor from generator | self-grading degrades without external grounding | zylos.ai; AI Engineer; Huang | E,Y |
| Fuse signals → calibrate → conformal abstain | no single signal calibrated alone; gives error-budget guarantee | UniCR 2509.01455 | E |
| Confidence-weighted vote (within-question) | cuts self-consistency samples >40% | CISC 2502.06233 | E |
| VC + self-consistency, N=2 | strong calibration cheaply | VCSC openreview | E |
| Abstention as rewarded first-class action | tune coverage↔reliability w/o retraining | I-CALM 2604.03904; HALO | E,R |
| Claim decomposition → per-claim NLI vs context | answer-level scoring misses cherry-pick/sycophancy | FutureAGI; RAGAS | E |
| Dedicated FCS model (HHEM) over LLM-judge | ~10pp better, calibrated 0–1, cheaper | Vectara | E |
| Ensemble-NLI meta-classifier → abstain | calibrated post-hoc verifier | HALT-RAG 2509.07475 | E |
| Keep factuality ≠ faithfulness | retrieval presence inflates confidence | FRANQ 2505.21072 | E |
| Verify claim→source after generation | post-hoc > prevention; composes | Mayo Reverse RAG; tianpan | R,E |
| Rerank top-50→5 before answering | −60% wrong-context answers | r/Rag practitioner | R |
| Tiered guardrails + parallel/stream | avoid 3.8s/9s p95 refusal tax | tianpan.co | E |
| Small distilled judges inline | 0.88–0.95 acc, ~97% cheaper | zylos.ai | E |

---

## Recommendations for Clarion (mapping to the invariant + the open decisions)
Clarion's invariant — *"no fact without a source, no action without a yes"* — is, in this literature's terms, a **faithfulness + selective-prediction** system. The research validates the architecture and points at three concrete upgrades.

**A. "Is the *choice* (which control) confident enough to act?"** — the pending `ContextRanker`/full-map decision.
- Don't rely on the LLM's single pick. Compute a **confidence from fused signals**: (1) **self-consistency dispersion** — sample `decide_step` a few times (N=2–3 is enough [VCSC]); if it picks different controls, that's low confidence; (2) **retrieval/goal compatibility** — semantic similarity of the picked control to the goal (this is the embedding `ContextRanker` doubling as a confidence signal, not just a filter); (3) optional small-judge check.
- **Abstain-on-ambiguity** instead of guessing: if the top two candidates are close (dispersion high or similarity near-tie), *read the options back and ask* — the I-CALM "abstention as a first-class, rewarded action" pattern, and Clarion's own "say when you can't find it" principle. This is the missing piece flagged in the earlier design discussion.
- The **consent readback naming the grounded control** is your conformal backstop — the human is the final calibrated check on the pick.

**B. "Is the spoken *fact* grounded?"** — the epistemic clause.
- Treat **one spoken `Fact` = one atomic claim that must have a non-null supporting node/chunk** (FutureAGI's claim-level grounding maps 1:1 onto `source_node_id`). This is already the invariant — formalize the check as per-claim NLI/entailment against the retrieved/perceived context before speaking, especially for any *synthesized* line.
- For **negatives** ("no late fee"), the NLI **contradiction** check is the mechanically reliable one [E tianpan taxonomy] — Clarion's NegativeVerifier should look for an *entailing* "absent" statement and refuse otherwise (which it does); this literature backs that design.
- Keep **factuality out of it** — FRANQ's warning is the sharpest argument for Clarion's stance: never let "this is probably true" substitute for "this is on the page." Don't let retrieval presence inflate confidence.

**C. Cost-aware verification (matches the <800ms voice budget).**
- Use the **tiered** pattern: Tier 0 = the existing code guards (`reasoner_guard`, membership fence) — deterministic, µs; Tier 1 = a small/local NLI or the embedding-similarity confidence — tens of ms; Tier 2 = an LLM-judge groundedness pass **only when Tier 1 is uncertain or before an irreversible/high-stakes read**. A dedicated FCS model (HHEM-2.1-Open, T5, local) is a strong Tier-1/2 option that avoids an LLM-judge round-trip.
- Prefer **post-hoc verification** of the spoken line over trying to prevent it in the prompt.

---

## Ready-to-paste scaffolds (reconstructed from the legs — verify before shipping)

**1. RAGAS-style per-claim faithfulness (the grounding check), [E ragas]:**
```python
# 1) decompose the response into atomic claims (one LLM call)
# 2) NLI-verdict each claim against the retrieved/perceived context (one LLM call)
class StatementFaithfulnessAnswer(BaseModel):
    statement: str   # the claim, verbatim
    reason: str      # why supported / not
    verdict: int     # 1 = entailed by context, 0 = not
class NLIStatementInput(BaseModel):
    context: str             # the retrieved chunks / perceived page region
    statements: list[str]
# faithfulness = sum(verdict==1) / len(statements)
# hallucination_rate = sum(verdict==0) / len(statements)   # abstain if any spoken claim == 0
```

**2. SelfCheckGPT-NLI self-consistency (no ground truth needed), [E selfcheckgpt]:**
```python
from selfcheckgpt.modeling_selfcheck import SelfCheckNLI
selfcheck_nli = SelfCheckNLI(device=device)           # DeBERTa-v3-large MNLI
scores = selfcheck_nli.predict(                        # Prob(contradiction) per sentence
    sentences=sentences,                               # the candidate answer, split
    sampled_passages=[s1, s2, s3],                     # N stochastic re-generations
)   # e.g. [0.33, 0.97] -> 2nd sentence likely hallucinated -> drop/abstain
```

**3. Confidence-from-dispersion for control selection (Clarion-specific, derived from CISC/VCSC + UniCR):**
```python
# sample the decider a few times; agreement = confidence (within-question)
picks = [await reasoner.decide_step(goal, candidates, facts, hist) for _ in range(3)]
top = Counter(p.target_index for p in picks).most_common()
agree = top[0][1] / len(picks)                         # 1.0 = unanimous
margin = sim(goal, candidates[top[0][0]].name) - (sim(goal, candidates[top[1][0]].name)
                                                   if len(top) > 1 else 0)
confident = agree >= 0.67 and margin >= TAU            # else -> read options back & ASK (abstain)
```

**4. Tiered groundedness budget [E tianpan]:** Tier0 deterministic fences (µs) → Tier1 local NLI/embedding-sim (20–60ms) → Tier2 LLM-judge/HHEM only on the uncertain slice; run input checks in parallel, stream output checks.

---

## Numbers worth verifying
- Reranking top-50→5 "**cut wrong-context answers ~60%**" [R Rag 1pxe9jg] · wisent-guard **43%** detection on unseen TruthfulQA [R 1jqawj1] · Stanford legal RAG "**~65% at best**" [R 1f61oxc].
- CISC "**>40%** fewer reasoning paths" [E 2502.06233] · SMART "**up to 46%** truthfulness gain" [Y Huang] · small judges "**0.88–0.95** acc, **~97%** cheaper" [E zylos.ai].
- HALT-RAG F1 **0.98 QA / 0.78 summ / 0.74 dialogue** [E 2509.07475] · HHEM "**~10pp** > LLM-judge," 75.8% SummaC [E Vectara].
- Refusal tax **1.4s → 3.8s median / >9s p95**; classifier 20–60ms; "over-engineered if Tier-2 >5% traffic or +80ms p95" [E tianpan].
- Leaderboard hallucination rates (May 2026): top 1.8%; claude-haiku-4-5 9.8%, claude-sonnet-4-6 10.6%, claude-opus-4-5 10.9% [E Vectara].

## Next moves
- **Experiment:** add a dispersion+similarity confidence + abstain-on-ambiguity to `decide_step` (scaffold #3) and measure how often it correctly refuses on the gov-page "Food assistance" case vs. silently mis-picks.
- **Experiment:** wire a Tier-1 per-claim NLI (or local HHEM-2.1-Open) groundedness check before any *synthesized* spoken line; confirm it never blocks a plain `source_node_id`-backed read.
- **Follow-up search if needed:** first-party Anthropic Claude Citations / OpenAI groundedness docs (Exa missed these — Vectara dominated the vendor slice), and a head-to-head ms/$ benchmark of NLI-model vs LLM-judge vs HHEM.

## Sources
### Reddit [R]
- I killed RAG hallucinations (Docling+hybrid+bge-reranker+RAGAS) — r/Rag — https://www.reddit.com/r/Rag/comments/1pxe9jg/i_killed_rag_hallucinations_almost_completely/
- wisent-guard latent-space guardrails (43%) — r/LocalLLaMA — https://www.reddit.com/r/LocalLLaMA/comments/1jqawj1/open_sourcing_latent_space_guardrails_that_catch/
- Stanford "65% at best" — r/LocalLLaMA — https://www.reddit.com/r/LocalLLaMA/comments/1f61oxc/according_to_stanford_even_prograde_rag_systems/
- HALO-Loss abstention — r/MachineLearning — https://www.reddit.com/r/MachineLearning/comments/1skzuhd/i_dont_know_teaching_neural_networks_to_abstain/
- Hallucination detection via information theory — r/MachineLearning — https://www.reddit.com/r/MachineLearning/comments/1iu9ryi/r_detecting_llm_hallucinations_using_information/
- Mayo "Reverse RAG" — r/LLMDevs — https://www.reddit.com/r/LLMDevs/comments/1j9yk4e/mayo_clinics_secret_weapon_against_ai/
- The one-token (logprob) trick — r/LLMDevs — https://www.reddit.com/r/LLMDevs/comments/1k0nfnv/the_onetoken_trick_how_singletoken_llm_requests/
- Every LLM metric / Top 6 eval frameworks — r/LLMDevs — https://www.reddit.com/r/LLMDevs/comments/1j6pxv9/every_llm_metric_you_need_to_know/ · https://www.reddit.com/r/LLMDevs/comments/1i6r1h9/top_6_open_source_llm_evaluation_frameworks/
- RAG still hallucinates with good chunking — r/LLMDevs — https://www.reddit.com/r/LLMDevs/comments/1plynfw/rag_still_hallucinates_even_with_good_chunking/
- Why Language Models Hallucinate (OpenAI) — r/MachineLearning — https://www.reddit.com/r/MachineLearning/comments/1namvsk/why_language_models_hallucinate_openai_pseudo/
### YouTube [Y] (deep-links keep MM:SS)
- Adversarial evaluator / "self-eval is a trap" — AI Engineer (Anthropic workshop) — https://youtu.be/mR-WAvEPRwE?t=1110 · contract-first https://youtu.be/mR-WAvEPRwE?t=1475 · don't share traces https://youtu.be/mR-WAvEPRwE?t=3583
- SMART / entropy-MCTS / binary auditor — AI Agent Frontier (Lifu Huang) — https://youtu.be/UYAfS9xy5Tw?t=1102 · https://youtu.be/UYAfS9xy5Tw?t=1010 · https://youtu.be/UYAfS9xy5Tw?t=3032
- Agent self-awareness gap (knowing when to abstain) — Anthropic, Claude Plays Pokemon — https://youtu.be/CXhYDOvgpuU?t=1551
### Exa [E]
- FutureAGI RAG faithfulness deep dive 2026 — https://futureagi.com/blog/evaluating-rag-faithfulness-deep-dive-2026/
- UniCR (fuse→calibrate→conformal refuse) — https://arxiv.org/html/2509.01455v2
- Vectara HHEM + leaderboard — https://docs.vectara.com/docs/hallucination-and-evaluation/hallucination-evaluation · https://github.com/vectara/hallucination-leaderboard
- SelfCheckGPT — https://github.com/potsawee/selfcheckgpt
- RAGAS Faithfulness — https://github.com/explodinggradients/ragas/blob/main/docs/concepts/metrics/available_metrics/faithfulness.md
- HALT-RAG — https://arxiv.org/html/2509.07475v1 · CISC — https://arxiv.org/html/2502.06233v2 · VCSC — https://openreview.net/forum?id=66D3rZrNjV · FRANQ — https://arxiv.org/html/2505.21072v3 · I-CALM — https://www.arxiv.org/pdf/2604.03904
- LLM-as-judge in production — https://zylos.ai/research/2026-04-10-llm-as-judge-production-agent-verification-2026
- Refusal-latency tax / tiered budget — https://tianpan.co/blog/2026-04-28-refusal-latency-tax-safety-layers-budget · detection pipeline/taxonomy — https://tianpan.co/blog/2026-04-10-hallucination-detection-pipeline-production
- Eval frameworks compared — https://atlan.com/know/llm-evaluation-frameworks-compared/ · LongTracer — https://dev.to/muzammil_endevsols/longtracer-open-source-rag-hallucination-detection-without-llm-as-a-judge-39eg · AWS detect-hallucinations — https://aws.amazon.com/blogs/machine-learning/detect-hallucinations-for-rag-based-systems/

## Method notes
- Legs run: A (Reddit, 145 threads / 7 calls) · B (YouTube yt-rag) · C (Exa, 53 pages / 15 calls). No A/B WebSearch probe (deep dive).
- **Empty/thin:** Reddit site-wide scan returned r/Python noise (off-topic). **YouTube corpus is genuinely thin/off-topic** for this query — the in-scope namespaces (`yt_self_improving_agents`, `yt_web_agent_capture`) cover agent RL/optimization and GUI grounding, not confidence calibration or RAG faithfulness; it yielded useful *patterns* (adversarial eval, entropy-as-uncertainty, separate auditor) but **no** calibration metrics, RAG-faithfulness tooling, or abstention thresholds. Concrete methods come from Reddit + Exa.
- No YouTube ingest/enrichment was run (not requested; ingestion mutates the global corpus).
