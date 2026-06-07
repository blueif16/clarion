# Clarion — Document & Image Content Extraction (DESIGN → PROPOSAL)

_Status: **PROPOSAL — awaiting approval. Nothing below is implemented.** Authored 2026-06-07.
Folds the 2026-06-07 design conversation into one spec. Neighbours: the live Unsiloed probe
(`agent/probes/unsiloed/`, memory `unsiloed-parse-fidelity`), the structure knowledge layer
(`app/site_indexer.py`, `app/auto_index.py`), and the entailment-grounder work
(`docs/clarion-confidence-grounding-plan.md` §P4 — the same faithfulness axis, a different
modality). Index-design rule: `docs/research/moss-index-design.md`._

> One-line thesis: a chart/table/PDF is **content, not structure**. We index that a document
> *exists* in the background (cheap, value-blind), but we **read what's inside it on demand,
> goal-driven**, and a value extracted from it is speakable **only when it was extracted, not
> estimated, and its label↔value association is structurally certified** — otherwise we decline
> honestly and reroute to a higher-fidelity source. This is the kernel invariant, applied to a
> new modality — a *sharpening*, not a loosening.

---

## 0. Why this exists (the gap)

The auto-index (`app/auto_index.py`) and the site crawler (`app/site_indexer.py`) populate the
**structure** knowledge layer: PUBLIC, cookie-less, goal-less, value-blind by construction
(`describe_page` drops every StaticText node so a `$84.32` literally cannot enter the index).
That is correct for "where is the thing." It says **nothing** about the *data on the page* —
the downloadable fees table, the chart of monthly costs, the embedded PDF. For a blind/low-vision
user, that content is often exactly the high-stakes thing they cannot verify visually. "There's a
table here but I can't read it" abandons them at the moment that matters most.

The live Unsiloed probe (`agent/probes/unsiloed/parse_probe.py`, probed 2026-06-07) proved we
*can* read it — with caveats that turn out to be load-bearing:

- Labeled/categorical charts → values read EXACTLY (conf 0.94–0.99), with axes/legend/trend.
- Charts return as `Picture` segments — numbers live in the VLM `markdown`, not a `Table`.
- **PDFs give a real structured `Table` segment**; the SAME table in a raster folds into the
  Picture prose (conf drops to 0.76). → structure survives in PDF, is destroyed in raster.
- **Danger case:** label-less charts (scatter) → the VLM **ESTIMATES** point values ("approximate").
- Cost/latency: **1 credit/image, ~9–18 s/job** (per-page for PDF) → ~20× the <800 ms turn budget.

So the work is: a goal-driven, on-demand extraction path that is **epistemically honest by
construction** and **paced so the latency is legible** to a voice-first, eyes-free user.

---

## 1. The kernel invariant, sharpened (provenance vs. fidelity)

The product invariant (foundation, LOCKED): **No fact without a source.** Today that is enforced
structurally — a speakable `Fact` carries a real `source_node_id` (`state.py:60`), and the
firewall against memory is *type-level*: a `Recall` has **no `source_node_id` field at all**, so it
cannot be admitted by the VERIFY membership fence (`clarion-memory-design.md` §5).

That implementation fused two properties the live-AXTree path happened to deliver together:

1. **Provenance** — is there a real, citable source handle?
2. **Fidelity** — is the value at that handle *what was actually read*, or a *guess*?

Unsiloed forces them apart. A parsed **PDF table cell has legitimate provenance** — `Passage.ref`
is a citable handle, exactly like a Moss KB passage (which we already speak). It is verbatim
*extracted* text. The **only** thing different about Unsiloed is that *some* outputs are not
extraction but **estimation** (the label-less bar's height read off pixels). Estimation has
provenance but no fidelity.

So the rule is **not** "never ground content." It is:

> "No fact without a source" was always shorthand for **"never speak a value the system did not
> actually read."** Extraction = reading. Estimation = guessing. The line moves from *"AXTree vs.
> everything else"* to **"extracted vs. generated,"** which is the line the kernel already draws
> everywhere — `PairedFact.backs()` is byte-identical *extract-don't-generate* (`state.py:129`),
> the negative-verifier is closed-world, `value_ref` is an enum over real Fact ids. Estimated
> chart values are *generated* → they fall on the already-forbidden side. Verbatim cells are
> *extracted* → the already-allowed side.

**This sharpening updates the prior blanket** ("Unsiloed output must never feed the live GROUND
path", memory `unsiloed-parse-fidelity`) to the tiered rule below. The blanket was a conservative
placeholder for a throwaway probe; the principled version is fidelity-tiered.

---

## 2. The taxonomy — three categories, not "charts"

A chart is not one fact; it is a title + axes + legend + data points + a shape, each with
**different fidelity**. The unit of grounding is never the image — it is the **claim** (`Fact`)
or the **pairing** (`PairedFact`). So content decomposes into three categories:

| Category | What it yields | Speakable? |
|---|---|---|
| **Table** (PDF/HTML `Table` segment) | structured cells in rows/cols | **Yes** — cell text is verbatim; row/col membership is a real structural pairing method → cited "row X." |
| **Chart** (raster `Picture` segment) | verbatim spans (title, axis labels, legend) + values | **Partial** — verbatim spans speak; specific value↔label claims **do not** (no structural pairing); **escalate** to the data behind it. |
| **Semantic image** (photo, diagram, infographic) | a VLM **description** | **No (as fact).** A description is *generated interpretation* — spoken only explicitly marked ("the image appears to show…"); any actionable claim must be **re-grounded on the live page** (the same "recall is a hint" firewall). |

**Default routing (format-based, not lexical):** prefer the structured source — **PDF/Table > raster
image**. Treat raster as lower-fidelity; when an image will not parse cleanly, **escalate** (find the
download/PDF/"view as table" behind it). This is a *format* fidelity ordering (PDF preserves the
association structure, raster destroys it), not a banned keyword heuristic.

**Decorative skip is a contract, not a heuristic.** `role="presentation"`, `aria-hidden="true"`,
empty `alt=""` are W3C-defined decorative signals. Skipping them honors the accessibility contract
— it is structural, allowed, and on-brand. "Is this table/chart *relevant to the goal*?" is the
opposite kind of question — that is **meaning**, decided by the Reasoner/embeddings, never an
`if "summary" in caption` table.

---

## 3. The two structural gates (stacked)

Speakability of an extracted value is decided by two gates that compose. Neither is a confidence
dial — both are structural, so they cannot silently erode.

### Gate 1 — per-span, at the adapter: verbatim or estimated?
A title/axis-label/legend entry is *printed text* → OCR'd verbatim → mints a real `ref`. An
unlabeled bar's height is *estimated from pixels* → **no usable `ref`** (empty), exactly like
`source_node_id = None`. **The existing membership fence then refuses it for free** — we add no new
kernel check. The adapter is the *sole minter of refs*, and it mints one only for the verbatim tier.

### Gate 2 — per-pairing, in the kernel: can the label↔value association be certified?
A `PairedFact` requires a structural `method` ∈ {`aria-labelledby`, `for`, `shared-row`,
`dom-ancestry`} — **never reading-order proximity** (`state.py:118`, the thing that mis-pairs). A
flat rasterized chart **has no such structure** → it can never mint a valid `PairedFact` → **"Q4 is
$35" off a chart image is ungroundable by construction**, even if both halves were read perfectly.
A real `Table` segment **has** rows/cols → cell membership *is* `shared-row` → its pairing is
certified → speakable. This is exactly why the probe found PDFs give `Table` segments and rasters
fold into prose: **the PDF kept the structure that certifies the pairing; the raster lost it.**

The estimated-value case is a *subset* of "no certified pairing" — it fails Gate 1 too.

### The safety asymmetry (why this is safe to ship)
The default is refusal, and the failure modes are lopsided:
- Wrongly **downgrade** a true cell → decline to speak a true value → annoying, **not a lie**. Fine.
- Wrongly **upgrade** an estimate → speak a guess as fact → **the catastrophe.**

So the tier classifier is **conservative: estimated unless structurally certain it is verbatim** (a
real `Table` segment, high confidence, structured cells). Same asymmetry as the rest of the kernel:
when unsure, refuse/escalate, never assert. The reframe opens a *narrow, conservatively-gated*
channel for the unambiguously-real case and leaves everything ambiguous on the forbidden side.

---

## 4. The `DocumentExtractor` port + contract changes (minimal, additive, reversible)

`contracts/` and `kernel/` stay SDK-free; every change is additive/default-valued so the frozen
test gate stays green by construction. Unsiloed is a **provider → it lives behind a port in an
adapter**, never in the kernel (the §6 invariant). Note the existing `Ingest` port
(`ports.py:197`, "Parse company docs… (Unsiloed)") is *named* for this but its live impl
(`GeminiMossIngest`) actually does **text → Moss embed**, not file parsing — so the parse concern
gets its **own** port to avoid overloading a seam that already means something else.

### 4a. New port — `DocumentExtractor` (`contracts/ports.py`)
```python
class DocumentExtractor(ABC):
    """On-demand parse of a FILE (PDF/image bytes) → fidelity-tagged passages
    (Unsiloed). Distinct from `Ingest` (text → Moss embed): this READS content
    and tags how trustworthy each span is. Returns `Passage`s; the adapter is the
    SOLE minter of `ref` — it mints one ONLY for the verbatim tier (Gate 1)."""
    @abstractmethod
    async def extract(self, doc: bytes, *, mime: str = "") -> list[Passage]: ...
```

### 4b. Extend `Passage` (`contracts/state.py`) — additive
```python
class Passage(BaseModel):
    text: str
    ref: str                       # EMPTY for the estimated tier → unspeakable by the existing fence
    score: float = 0.0
    metadata: dict[str, str] = Field(default_factory=dict)
    # NEW — additive, default keeps every existing Passage(...) unchanged:
    fidelity: Literal["verbatim", "read", "estimated"] = "verbatim"
    segment_type: str = ""         # "Table" | "Picture" | "Formula" | … (audit / UX)
```
`fidelity` is **advisory/UX/audit** — it lets the voice plane honestly say *"I'm estimating"* vs.
*"I can't read this."* It does **not** weaken the firewall: enforcement is the empty `ref`
(`estimated` ⇒ `ref == ""`), not a field the gate must remember to check.

### 4c. Pairing constraint (adapter discipline + the existing fence)
The adapter mints a `PairedFact` **only** from a real `Table` segment (`method="shared-row"`),
**never** from a flat `Picture`. A chart-derived "X is Y" claim therefore has no `PairedFact`, and
`PairedFact.backs()` (`state.py:129`) already refuses it. No kernel change.

### 4d. Triggering the extract (one open decision — §7)
The Reasoner decides each step as one of four action kinds (`click`/`fill`/`navigate`/`read`); today
`read` means "read text off the live AXTree" (cheap, instant). When the goal needs the *contents of a
document*, how does the heavy Unsiloed parse get invoked?

- **Option A (recommended): a new `Action.kind = "extract"`.** The Reasoner explicitly decides "read
  inside that document." The decision is *the model's* and shows up in the trace as its own step; an
  *additive* enum member (existing kinds and their code paths unchanged; new paths fire only on the
  new kind). Reasoner adapters + the guard learn one new kind.
- **Option B (lower footprint): "read routing."** The Reasoner keeps emitting plain `read`; the
  **kernel** inspects the target node and, if it is a downloadable doc/figure (by **AX role** —
  structural, allowed), routes that *same* `read` to the extractor instead of a plain AX read. One
  verb, two execution paths chosen by the kernel. No contract change, but the routing is implicit.

| | Option A (`extract`) | Option B (read routing) |
|---|---|---|
| Contract | new enum member (additive) | **no contract change** |
| Who decides | the **model**, explicitly | the **kernel**, implicitly from node type |
| Trace | "extract" visible as its own step | looks like a `read` |
| Risk | model must learn when to pick it | `read` now *sometimes* secretly means a 9–18 s job |

**Recommend A:** the project thesis is *"the LLM is the decider."* Reading inside a document is a
real, costly decision (9–18 s, a credit) — the Reasoner should *own* it and reason about whether it's
worth it, not have the kernel silently turn a "read" into a slow background op behind its back (which
also means the slow-path UX in §5 must fire from a routing branch the model never chose). Option B is
just less code.

Either way the **download itself is a `read`-class, reversible action** (a GET; no server state
change) → by `kernel/irreversibility.classify` it does **not** need a hard CONSENT gate. The "pause"
is a *read-back-and-confirm before speaking*, not consent-for-side-effect (§5).

---

## 5. The extraction-UX contract (the interaction half of the spec)

Extraction is ~9–18 s — it **cannot** run inline in a turn. The two-plane event model already makes
this work: `advance_task()` is non-blocking, the voice plane stays live, and a result surfaces via
an event when the background parse finishes. The interaction is governed by these rules.

### 5.1 For a blind user, silence is the bug
A sighted user gets a spinner; a blind user gets nothing. 9–18 s of silence reads as *"it crashed /
did it hear me / is it stuck."* So progress narration is **the honesty thesis applied to process**:
*say when you're busy*, just as we *say when we can't*. **But narrate waits and state-transitions,
not mechanics.** Test for any line: *does it help the user decide whether to wait, barge in, or
redirect?* "Reading the fees table now" passes; "submitting job, polling, segment 3 of 12" fails
(machine thinking out loud → erodes trust).

### 5.2 Timeline — commit in-budget, overlap cheap grounded signal with the expensive parse
1. **Close the turn with a commitment (≤800 ms):** *"Okay — reading that table now, give me a few
   seconds."* Converts silence-anxiety into expectation; "a few seconds" is a lightweight
   **opt-out** (the user can wave it off — a non-blocking consent-by-expectation, no `interrupt()`
   for a reversible read).
2. **While the parse runs, narrate structure we already have for free** (the link/page already gave
   the title, format, rough shape): *"It's a 3-page PDF, the section's called 'Schedule of fees.'"*
   Fill the dead time with **grounded** signal, never theatrical filler.
3. **Deliver the result answer-first** (§5.3), or the honest-partial/escalate path (§5.4).

> ⚠️ **Parallel narration MUST be grounded structure, never improvised.** Free-form filler to pass
> the time is *exactly* GAP-1 (the voice-plane ungrounded-narration gap, memory
> `voice-plane-ungrounded-narration-gap`): the moment the voice LLM riffs to fill silence, an
> ungrounded line can escape. "The voice agent doing something else" is fine **only** when the
> something-else is read back from grounded facts. This is the same DeliveryGate concern.

### 5.3 Deliver part-by-part — but lead with the answer, not a dump
Audio is linear; a blind user cannot skim. Progressive, **ordered by what they asked**:
> *"Got it — the late fee is $35."* ← the cell they wanted, first, cited.
> *"There are six other fees in that table — want me to read them?"* ← offer the rest, don't force it.

For a long multi-part extraction, **milestone** updates the user cares about ("nothing on fees on
page 1… found them on page 2") — never pipeline stages.

### 5.4 Honest-partial and escalate are first-class
- Unlabeled-but-useful chart → **partial readout**: *"It's a bar chart titled 'Monthly late fees,'
  Jan–Dec, y-axis in dollars. The bars aren't labeled with values, so I can't read exact amounts.
  There's a 'Download CSV' — want the real numbers from that?"* The decline **is** the product (the
  verifiable negative at sub-chart granularity); the escalate turns the dead-end into the
  download-the-table flow.
- Two plausible readings of a real cell → the **`alternatives`** clarify path (`state.py:285`): the
  model self-reports ambiguity, the kernel asks, never guesses.
- **Mislabeled source vs. extractor misread** (load-bearing distinction):
  - *Source* mislabeled (the chart itself says "Q4" over Q3's data): we read it **verbatim, cited**,
    and do **not** correct it. Our promise is *"this is what the page says,"* not *"this is true"* —
    correcting a source's error means *generating* a value the page doesn't show. The citation makes
    the source accountable.
  - *Extractor* misread (Unsiloed OCRs $35→$85, or mis-pairs): **our** problem — for charts Gate 2
    already refuses the pairing; for tables it routes to `alternatives` or falls to the estimated
    tier. The clean-citation-on-the-wrong-number is precisely what `PairedFact` exists to stop.
- **Trend/shape** ("fees are rising") stays on the **interpretation** side — spoken only explicitly
  flagged ("that's my read of the shape, not a labeled value"), never a flat fact. For high stakes:
  "I can describe the shape but not the numbers — let me get the data" beats a confident trend claim.

### 5.5 Keep the human in command of the *wait* itself
The voice plane is live during the background parse, so **barge-in must cancel**: "stop" / "never
mind" aborts the extraction (or drops the pending result). For a blind user mid-task, killing a slow
operation **is** "keeping the human in command at every consequential step" — applied to *time*, not
just side-effects. Wire it to the existing `on_barge_in` + `SpeechHandle.interrupted`.

---

## 6. Invariant preservation (by construction)

1. **Estimated ⇒ no `ref` ⇒ unspeakable** by the existing membership fence — no new kernel check.
   The adapter is the sole minter of `ref`.
2. **Chart pairings are ungroundable** — a flat `Picture` cannot supply a structural `method`, so
   `PairedFact.backs()` refuses any chart-derived "X is Y" claim. Only `Table` cells pair.
3. **Semantic-image descriptions are generated → never a `Fact`.** Spoken only marked as
   interpretation; any actionable claim is re-grounded on the live page (the recall firewall).
4. **No value is cached as a fact.** Extracted values live in the working set for the current goal,
   cited; they are never written into the structure index (category violation + "values never"
   retention). A stable *document* MAY be cached **parsed** — the fidelity-tagged passages saved so a
   re-read skips the 9–18 s parse — in a separate **content** category index (`clarion-docs`):
   - **Key = URL + content-hash of the bytes; the hash IS the freshness check** (verify-on-use, NOT
     TTL — the document's analogue of structure freshness). Same bytes → cached parse valid; bytes
     changed → hash differs → miss → re-parse. A document is a fixed blob, so its fingerprint is
     literally the byte-hash.
   - **Retention on the consent axis:** a PUBLIC doc's parse (a gov PDF) may be shared; a
     PRIVATE/authenticated doc's parse is consent-gated **per-user**, never written to the shared
     index.
   - **Changes nothing epistemically:** a cache hit re-applies the SAME fidelity gates — a cached
     verbatim cell is still cited, a cached estimated value is still unspeakable. The cache only
     skips the re-parse.

   *Deferred — §7:* re-reading the *exact same* document is rare for the event, so this is premature
   until a real re-read pattern shows up; the on-demand extractor works fully without it.
5. **CI enforcement (mandatory, no-network), mirrors the memory no-leak test:** a `FakeExtractor`
   round-trip asserts every `Passage` with `fidelity="estimated"` has `ref == ""`, and that no
   chart `Picture` segment ever yields a `PairedFact`. This is what keeps the guarantee from eroding
   across future sessions, and it needs no Unsiloed creds.

---

## 7. Open decisions (need your call)
1. **Confirm the fidelity-tier reframe** (§1) — verbatim-extracted, cited table cells become
   speakable like a KB passage; only the estimated tier stays structurally unspeakable. This flips
   the current "never GROUND content" memory to the tiered rule. *(You've indicated yes — this
   records it.)*
2. **Trigger shape** (§4d): new `Action.kind="extract"` (explicit, recommended) vs. routing a
   `read` on a document affordance (lower footprint, implicit).
3. **Parsed-document cache** (§6.4): build `clarion-docs` now, or defer until a real PDF re-read
   shows up (recommend defer — the live re-parse is cheap relative to building the cache path).
4. **Latency budget for the commit line** — is a single "reading that now, a few seconds" enough, or
   do we want a periodic interruptible heartbeat past ~8 s?

---

## 8. Build plan (ordered, all additive)

| # | Commit | Notes | Effort |
|---|---|---|---|
| 1 | `feat(contracts): Passage.fidelity + segment_type; DocumentExtractor port` | ~15 lines pure pydantic/abc; default-valued → frozen gate stays green. | S |
| 2 | `feat(adapters): UnsiloedExtractor behind DocumentExtractor` | promote `probes/unsiloed/parse_probe.py` logic into `adapters/unsiloed_extractor.py`; map segment_type+conf+structure → fidelity tier (conservative); mint `ref` ONLY for verbatim; `Table`-only `PairedFact`. | M |
| 3 | `feat(fakes)+test: FakeExtractor + the fidelity-firewall CI test (§6.5)` | no creds; the no-leak teeth. | S |
| 4 | `feat(kernel): extract trigger (decision #2) + reversible-read gate path` | additive `Action` kind or `read`-routing; reasoner adapters + guard learn it. | M |
| 5 | `feat(voice): extraction-UX (commit line · grounded-overlay narration · answer-first delivery · barge-in cancel)` | §5; reuses `play_filler`/`say(interruptible)`/`on_barge_in`/`advance_task`; copy_lint the new lines. | M |
| 6 *(opt, deferred)* | `feat(retrieval): clarion-docs parsed-document cache (content-hash freshness)` | §6.4; only if a real re-read warrants it. | M |

**Net-new is small** and lands behind the existing seams (port + adapter + voice plane). Auto-index
(`app/auto_index.py`) and the structure crawler stay **exactly as-is** (structure, detect-not-parse).

---

## 9. References
- **Probe + fidelity findings:** `agent/probes/unsiloed/` (`parse_probe.py`, `fixtures/ground_truth.json`,
  `out/`); memory `unsiloed-parse-fidelity` (to be updated to the tiered rule).
- **Invariant + firewall template:** `CLAUDE.md` (the invariant); `docs/clarion-memory-design.md` §5
  (the type-level `Recall` firewall this mirrors); `contracts/state.py` (`Fact`, `PairedFact`, `Passage`).
- **Neighbour grounder (same faithfulness axis):** `docs/clarion-confidence-grounding-plan.md` §P4.
- **Structure layer (the thing this is NOT):** `app/auto_index.py`, `app/site_indexer.py`,
  `app/structure_freshness.py`; `docs/research/moss-index-design.md` (one index per category).
- **Voice-plane grounding gap (the §5.2 constraint):** memory `voice-plane-ungrounded-narration-gap`.
