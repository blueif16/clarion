# Clarion — User Memory & Completed-Workflow Store (DESIGN → BUILT)

_Status: **IMPLEMENTED 2026-06-06** on branch `feat/clarion-memory` (commits: contracts ·
ports · `memory_moss` · fakes + no-leak gate · `gov_proof` episode write · runtime+planner
recall · remember-gate logic). **Pending:** the voice remember-gate *surfacing*
(`voice_entry`, in-flight) + a live Moss episode round-trip. Storage = the current Moss
implementation._

_NB (post-design discovery, from the structure-index research): Moss **does** support
query-time metadata filtering (`QueryOptions.filter`), and the index cap is a pricing tier
(free=3, paid=unlimited) — so §0's "no metadata filter" / per-user-index reasoning is
**superseded**. The shipped impl keeps the per-user `clarion-mem-{user}` index (works;
single-user `"default"` for the event); a clean future simplification is ONE shared
`clarion-mem` index keyed by `user_id` + `kind` metadata filters._

This is backlog item #4 (the knowledge layer) in `docs/clarion-status.md`, scoped down
to what ships for the event. It persists **what the user has done and prefers** so the
**next** run plans faster, recalls preferences, and reminds the user what they consented
to last time — without ever eroding the kernel invariant.

---

## The kernel, extended with a third consent clause

The product invariant (foundation, LOCKED) is two clauses:
> **No fact without a source. No action without a yes.**

This design adds a third, at the **implementation** level (it does not rewrite the
locked foundation — it applies the same keep-the-human-in-command principle to the
privacy surface):

> **No memory without a yes.**

Nothing about the user is persisted unless they explicitly said "yes, remember that."
Memory is strictly opt-in. This is why preference capture is a consent gate, not a
hidden heuristic (§4).

---

## 0. Moss index budget (ample on the new project)

Storage is the **new Moss project `fe48810e-…`** (creds in `agent/.env`, switched
2026-06-06). Probed live: healthy, **1 index** (`clarion-kb`). The prior hard 3-index cap
that constrained project `946ad534-…` **no longer binds** on this account — headroom is
ample, so the layout below is chosen for **clarity**, not forced by a slot budget.

| index | role |
|---|---|
| `clarion-kb` | policy KB grounding (exists) |
| `clarion-mem-default` | **this design** — all user memory (facts + prefs + episodes), kind-discriminated |
| `clarion-site-<host>` | #4a crawler (deferred) — now free to grow per site |

Design choices this affects (now choices, not constraints):
- **One index for all memory kinds** stays the default for simplicity (one `get_docs` +
  a cheap client-side `kind` filter), but is no longer mandatory.
- **Single-user `"default"`** stays a deliberate choice (the demo is single-user), not a
  cap necessity. Multi-user is now feasible; if it lands, consolidating users into one
  `clarion-mem` index keyed by `user_id` metadata is cleaner than an index per user.
- `clarion-mem-default` does **not** exist on this project yet — `MossMemory.write_*`
  creates it on first write. The embed path must match `clarion-kb` (Gemini-custom,
  `MOSS_EMBED_MODEL` unset).

---

## 1. What we store — one shared Moss index, three doc kinds

**Index granularity: the per-user index that already ships (single-user → one index).**
`MossMemory._index_for(user_id)` → `clarion-mem-{safe_user_id}` (`memory_moss.py:32`).
All three kinds co-habit that single index. Moss has **no metadata field filter** beyond
`get_docs` (list all), so:
- **User scope** = the index *name* (one index per user).
- **Kind partitioning** = client-side, after one `get_docs`, by reading `metadata["kind"]`.
- **Fuzzy recall** = `search()` on the in-memory-loaded index, then a client-side filter
  of the returned hits on `kind`.

**Embedding:** follow the existing branch (`memory_moss.py:56-58`) — built-in Moss model
when `builtin_embed_model()` is set, else Gemini `gemini-embedding-001` custom vectors.
All kinds in one index **must** match whatever that index was built with (you cannot mix
built-in/custom in a single index).

**Binding (decided):** single-user — `user_id="default"` (`memory_moss.py:53`). A
deliberate choice (the demo is single-user); with the cap lifted (§0) multi-user is
feasible later, ideally consolidated into one `clarion-mem` index keyed by `user_id`
metadata rather than an index per user. No per-user threading for the event.

### 1a. `kind="fact"` — unchanged CONFIRM write-back (+1 metadata key)

| field | value |
|---|---|
| index | `clarion-mem-{user}` |
| doc id | `"{index}::{sha1(value)[:12]}"` (today's id, `memory_moss.py:65-68`) |
| text | `fact.value` |
| metadata | `{kind:"fact", source_node_id, polarity, verified, written_at}` — **today's dict + `kind`** |
| keyed by | `value` (content hash) |

Behaviour is exactly today's; the only change is stamping `kind:"fact"`. A fact is the
**only** kind that legitimately carries a real `source_node_id`.

### 1b. `kind="preference"` — a remembered standing trait

| field | value |
|---|---|
| index | `clarion-mem-{user}` |
| doc id | `"{index}::pref::{sha1(pref_key)[:12]}"` — keyed on **`pref_key`, so a new value UPSERTS** (no stale-variant pileup) |
| text | `"{pref_key}: {pref_value}"` (natural language → semantically recallable) |
| metadata | `{kind:"preference", pref_key, pref_value, source_node_id:"" (ALWAYS empty), origin:"stated", written_at}` |
| keyed by | `pref_key` (free-text label the Reasoner derives from the field — no per-site registry) |

`source_node_id` is hard-coded empty: a preference is **never** page-grounded.

### 1c. `kind="episode"` — the completed-workflow record

| field | value |
|---|---|
| index | `clarion-mem-{user}` |
| doc id | `"{index}::ep::{sha1(goal_norm + "\|" + url_host)[:12]}"` — **UPSERTS the latest good path** per (goal, site) |
| text | `goal + " \| " + " · ".join(s.description for s in subgoals) + " " + plan_utterance` — goal+plan in words, so a NEW goal semantically recalls the nearest past path. **No grounded value ever in the text.** |
| metadata | `{kind:"episode", goal, goal_norm, url_host, outcome:"completed"\|"declined", subgoals_json, plan_utterance, consent_json, hard_stops, approvals, decide_ms_mean, perceive_ms_mean, completed_at, n_subgoals, schema:"v1"}` |
| keyed by | `(goal_norm, url_host)` |

`url_host = urlparse(url).hostname` — generic, never a site registry. "Similar site/goal"
matching is the embedding's job. Moss metadata is `dict[str,str]`, so `subgoals`/`consent`
are JSON-encoded into single string fields. The bulky AXTree is **never** stored.

**Store-side invariant:** the episode persists `ProofResult.subgoals` / `plan_utterance` /
`consent_events` but **deliberately omits `ProofResult.grounded_values`** (the
`source_node_id`-bearing facts, `gov_proof.py:409`). An episode is structurally incapable
of carrying a citable page value.

---

## 2. The write path (episodes)

**Where:** `app/gov_proof.py`, `GovProofDriver.run()`, **right after the harvest block
(lines 408-414), before `return self._finalize()` (415).** `_finalize()` (417) is only the
reasoner-stats formatter; the run artifact lands in `run()` at 408-414.

**Trigger:** a run that reached a terminal state without a fatal error — `outcome` in
`{"completed","declined"}` (a `declined` hard-stop is a first-class success: the gate fired
correctly). **Skip on `error`** so a crashed run is never replayed.

**How:** gated by `CLARION_MEMORY=1`, wrapped in `try/except` so a memory failure never
fails the run, and **fire-and-forget** (does *not* `await wait_for_job` — nothing re-reads
it this session and it sits at run-finish, off the turn budget).

```python
# after gov_proof.py:414, before return self._finalize()
if self.rt.memory is not None and os.environ.get("CLARION_MEMORY") == "1":
    try:
        await self.rt.memory.write_episode(self.rt.user_id, WorkflowEpisode(
            goal=self.goal,
            url_host=(urlparse(self.url).hostname or ""),
            subgoals=self.result.subgoals,
            plan_utterance=self.result.plan_utterance,
            outcome=_outcome(self.result),            # completed | declined
            consent=[_remembered(c) for c in self.result.consent_events],
            hard_stops=self.result.hard_stops,
            approvals=self.result.approvals,
            decide_ms_mean=_mean(self.result.decide_ms),
            perceive_ms_mean=_mean(self.result.perceive_ms),
            completed_at=time.time(),
        ))
    except Exception as exc:                          # never fail the run on a memory miss
        _p(f"  [memory] episode write skipped: {exc}")
```

Facts keep their existing **synchronous** CONFIRM write-back (`MossMemory.write`, now
stamping `kind:"fact"`) for read-back immediacy (R3).

---

## 3. The reuse path (next run plans faster)

**Where:** `stages/graph.py` — the **PLANNER node, once per run, right before `plan_goal`.**
`HeroRuntime` (`app/runtime.py`) gains optional `memory` + `user_id`.

**Recall returns a typed `Recall` bundle — never `list[Fact]` — and lives on the `Memory`
port, never `Retriever`.** This is load-bearing: `retriever_moss.py:108` literally sets
`source_node_id=hit.id`, so routing recall through the Retriever would **forge a live
source** onto a remembered value. Recall must never touch that path.

```python
# graph.py PLANNER node, before plan_goal(...)
recall = None
if rt.memory is not None and os.environ.get("CLARION_MEMORY") == "1":
    recall = await rt.memory.recall(rt.user_id, goal=goal, url_host=host, k=3)
subgoals = await reasoner.plan_goal(
    goal, orient, affordances,
    prior_plan_hint=(recall.plan_hint if recall else None),   # advisory only
)
```

**The loop — find best episode → propose plan → re-ground live:**
1. **Find:** `load_index` + hybrid `search(index, query=goal, top_k=3, alpha≈0.6)` (~1-10ms),
   filter hits to `kind=="episode"`, take the nearest above a similarity floor; decode
   `subgoals_json` → `list[Subgoal]` into `Recall.plan_hint`.
2. **Propose:** the recalled subgoals ride into `Reasoner.plan_goal` as an **advisory
   `prior_plan_hint`** — a new optional kwarg read only by the Gemini/Minimax *adapters*;
   the `Reasoner` ABC is unchanged. The Reasoner warm-starts from a known-good shape →
   fewer replans, less cold reasoning (which dominates `decide_ms`). `plan_utterance` gives
   the user an instant "last time we did X then Y" on-ramp.
3. **Re-ground live (DECIDED):** the Reasoner may accept, adapt, or discard the hint against
   the **live ORIENT readout + affordances**; the executor re-perceives and the normal
   GROUND→VERIFY path re-derives **every** value with a fresh live `source_node_id` before
   anything is spoken. A remembered value is **always re-read live, never re-spoken from
   memory** — even a stored `kind="fact"`.

**Consent recall:** at the gate, if a remembered decision matches the current step (by
`url_host` + step semantics), the readback appends a **spoken reminder** — "last time on
this site you declined the submit step." It NEVER pre-fills the decision: `interrupt()`
still demands a fresh explicit "yes." Recall is never consent.

---

## 4. Preference capture — the consent-gated "remember?" offer ("no memory without a yes")

Preferences are captured **only** through an explicit consent gate, **batched at the end of
a completed flow** (decided — one ask protects the turn budget and is a clean demo beat).

**Mechanism (reuses the existing `interrupt()` consent machinery — no new infra):**
1. On flow finish, the Reasoner **nominates candidate memory items** — reusable `(key,
   value)` pairs drawn from the inputs the user supplied (a payee, an address, a readback
   style). Generic: the key is the field's semantic label, no per-site list.
2. A **secret-suppression guard drops anything sensitive** — a value from a password/secret
   field (AX role + input type) or Reasoner-classified one-time/secret (OTP, CVV, full card
   number, SSN, a one-off amount) is **never offered**. We don't even ask.
3. The surviving candidates are surfaced as **one batched consent**: "I can remember your
   payee Northwind Electric and your autopay preference — keep either?"
4. On **"yes"** → `write_preference(user_id, key, value, origin="stated")`. On **"no" or
   silence** → nothing persists.

A captured "remember this value" is stored as a **preference** (`source_node_id=""`), not a
fact — so it re-enters the next run as a re-grounded *candidate*, never a spoken citation.

---

## 5. Invariant preservation (load-bearing, by construction)

"A `Fact` with `source_node_id=None` MUST NOT be spoken" survives by a **structural firewall
at the type level**, not by discipline at the speak site:

1. **The recall channel is a non-`Fact` type.** `recall()` returns a `Recall` (plan_hint +
   preferences + consent_recall) with **no `source_node_id` field anywhere**. The VERIFY
   membership fence only admits `Fact`s with `source_node_id != None`; it *cannot* accept a
   `Recall`, a `WorkflowEpisode`, or a preference string. A remembered value is structurally
   unspeakable.
2. **Recall lives on `Memory`, never `Retriever`** (which stamps `source_node_id=hit.id`,
   `retriever_moss.py:108`). A remembered value can never acquire a source it didn't earn live.
3. **The store carries no live citation.** Preferences are written `source_node_id=""`;
   episodes omit `grounded_values` entirely.
4. **Re-entry forces re-grounding (decided).** A recalled subgoal or candidate value injects
   ONLY as planner advice / a fill candidate; it is never written into `grounded_facts`.
   Before it is spoken or used in an irreversible step it is re-grounded against the live
   page (a NEW `Fact` with a fresh live `source_node_id`) and re-passes the consent gate.
5. **The agentic clause is untouched.** A remembered "approve" NEVER auto-approves — every
   irreversible step still hits a fresh live `interrupt()` and a fresh per-step "yes."

**CI enforcement (mandatory, no-network):** a `FakeMemory` round-trip test in the `.[test]`
gate asserts `recall(...)` never returns anything with a `source_node_id`, and no recalled
episode/preference ever appears in `grounded_facts`. This is the only thing that keeps the
guarantee from eroding across future sessions, and it needs no Moss creds.

---

## 6. Contract / port / adapter changes (file-by-file, minimal & reversible)

`contracts/` and `kernel/` stay SDK-free; every change is additive/default-valued, so the
82 frozen no-network tests stay green by construction. Deleting the new methods/fields
restores today's behaviour exactly.

- **`contracts/state.py`** — add pure pydantic value objects:
  - `ConsentRecord(BaseModel)`: `{proposal_id:str, utterance:str="", irreversible:bool=False,
    decision:str=""}` (the lean, contract-pure projection of `gov_proof`'s `ConsentEvent`).
  - `WorkflowEpisode(BaseModel)`: `{goal:str, url_host:str, subgoals:list[Subgoal]=[],
    plan_utterance:str="", outcome:Literal["completed","declined"]="completed",
    consent:list[ConsentRecord]=[], hard_stops:int=0, approvals:int=0,
    decide_ms_mean:float=0.0, perceive_ms_mean:float=0.0, completed_at:float=0.0}` —
    reuses the frozen `Subgoal`.
  - `Recall(BaseModel)`: `{plan_hint:Optional[WorkflowEpisode]=None,
    preferences:dict[str,str]={}, consent_recall:list[ConsentRecord]=[],
    similarity:float=0.0}` — **the firewall: no `source_node_id` field.**
  - Extend `Profile`: add `preferences:dict[str,str]={}` and
    `episodes:list[WorkflowEpisode]=[]` (both default-valued). Add new names to `__all__`.
- **`contracts/ports.py`** — extend the existing `Memory` ABC (do NOT add a parallel port)
  with three methods carrying **concrete no-op default bodies** (so `FakeMemory` and future
  adapters stay valid without implementing them):
  ```python
  async def write_preference(self, user_id, key, value, *, origin="stated") -> None: return None
  async def write_episode(self, user_id, episode) -> None: return None
  async def recall(self, user_id, goal, url_host, *, k=3) -> Recall: return Recall()
  ```
  `write(fact)` / `read_profile` are unchanged. `recall` returns a `Recall`, never
  `list[Fact]` — the signature itself prevents a remembered value entering as grounded.
- **`retrieval/memory_moss.py`** — the whole net-new surface, in the one existing adapter
  (~70 lines): stamp `kind:"fact"` in `write`; extract a `_upsert(index, doc, *, wait)`
  helper from lines 77-86 (`wait=True` facts, `wait=False` prefs/episodes);
  `write_preference`; `write_episode`; `recall` (`load_index` + `search` + `kind` filter,
  decode top hit into `Recall.plan_hint`, never reconstruct a `Fact`); branch `read_profile`
  on `meta["kind"]` to also populate `preferences`/`episodes`.
- **`fakes/adapters.py` (`FakeMemory`)** — add dict/list-backed `write_preference` /
  `write_episode` / `recall` so the no-network gate exercises write→recall round-trips and
  the no-leak assertion.
- **`app/runtime.py`** — `HeroRuntime` gains optional `memory:Optional[Memory]=None` +
  `user_id:str="default"` (defaults to `MossMemory(user_id=...)` when `CLARION_MEMORY=1`,
  else `None`); expose `self.memory` / `self.user_id`.
- **`stages/planner.py` + `graph.py` PLANNER node** — `plan_goal` accepts optional
  `prior_plan_hint:Optional[WorkflowEpisode]=None`, threaded into the Gemini/Minimax
  *adapter* prompts as advisory context (the `Reasoner` ABC stays unchanged); the node calls
  `recall(...)` once and stashes `consent_recall` on state for the gate.
- **`app/gov_proof.py`** — the `write_episode` call on the `run()` finish path + `urlparse`.

---

## 7. Build plan (ordered commits)

| # | Commit | New vs reused | Effort |
|---|---|---|---|
| 1 | `feat(contracts): ConsentRecord + WorkflowEpisode + Recall; extend Profile` | ~30 lines pure pydantic; reuses frozen `Subgoal`. 82 tests stay green (all default-valued). | S |
| 2 | `feat(ports): extend Memory ABC with write_preference/write_episode/recall (no-op defaults)` | ~6 lines; no SDK. | S |
| 3 | `feat(memory-moss): kind-discriminated docs; _upsert; write_preference/episode (fire-and-forget); recall; richer read_profile` | ~70 lines in the one adapter; reuses MossClient, `_index_for`, embed branch, metadata round-trip, `Fact` rebuild. | M |
| 4 | `feat(fakes)+test: FakeMemory round-trip + the no-leak invariant test` | ~40 lines + the mandatory CI assertion. No creds. | S |
| 5 | `feat(gov-proof): write_episode on run() finish (CLARION_MEMORY-gated, try/except, fire-and-forget)` | ~12 lines at gov_proof.py:414; reuses `ProofResult` harvest. | S |
| 6 | `feat(runtime+planner): thread memory/user_id; recall at PLANNER; prior_plan_hint into adapters; consent_recall reminder` | ~40 lines; `Reasoner` ABC unchanged. | M |
| 7 | `feat(remember-gate): end-of-flow batched "remember?" consent + secret-suppression → write_preference` | ~40 lines; reuses the `interrupt()` gate. | M |
| 8 *(opt)* | `test(live): one live-Moss episode write→recall round-trip (-m live)` | ~30 lines. | S |

**Net-new ≈ 130 lines** (excl. tests), all additive, all in adapter/app/contract-value
layers. **Reused:** the entire MossClient surface, `_index_for`, the upsert mechanics, the
embed branch, the metadata round-trip, the `Fact` rebuild, the frozen `Subgoal`, the
`ProofResult` harvest, `runtime.create` assembly, the PLANNER chokepoint, the consent gate.
**Overall: M, ~1.5 focused days.** Fully reversible.

---

## Decisions log (forks closed 2026-06-06)

- **Scope:** facts + preferences + episodes. **Site-functionalities store (#4a) deferred** —
  lowest ROI; the live loop re-perceives affordances anyway.
- **Recalled value:** **always re-ground live, never re-spoken from memory** (incl. stored
  facts).
- **Preference capture:** consent-gated **"remember?" offer, batched at flow end**; secrets
  never offered. → the third clause, *no memory without a yes*.
- **Binding:** single-user `"default"`.
- **Episode keying:** **upsert** by `(goal_norm, url_host)` (latest good path wins);
  `schema:"v1"` guard for a future switch to history.
- **Index:** one shared `clarion-mem-{user}` index for all kinds; filter by `metadata["kind"]`
  client-side. Dedicated memory index (not folded into `clarion-kb`).
- **Storage project:** Moss `fe48810e-…` (switched 2026-06-06); prior 3-index cap lifted —
  the index layout is a clarity choice, not a slot budget.
