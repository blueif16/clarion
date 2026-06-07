"""AG-PROVE — the GENERIC autonomous driver: the end-to-end proof that the Task
plane drives a full goal on a real site with ZERO site-specific code, every
invariant live (architecture migration **Step 3** acceptance + **Step 5**).

This RETIRES the hand-driven pay-topology harness. There is no AUTH→…→CONFIRM
script here, no demo-site creds, no per-stage predicate, no "Submit payment"
button name. The driver only:

  1. ``HeroRuntime.create(url, mode="normal", …)`` over a REAL ``PlaywrightActuator``
     + the LIVE ``MinimaxReasoner`` (MiniMax-M3, the de-hardcoding boundary), a
     same-provider MiniMax failover behind a 503-aware wrapper;
  2. ``runtime.build_stage_graph()`` — the generic executor (``stages.graph``:
     planner derives a goal-derived ``list[Subgoal]``; the kernel loop runs per
     subgoal; the done-check is the reasoner-SELECTED generic check evaluated in
     CODE against the re-perceived tree + a semantic anchor);
  3. drives it to completion ACROSS the parent consent ``interrupt()`` with an
     **autonomous consent policy** (SAFETY-CRITICAL): inspect each surfaced
     ``ConsentRequest`` — a NOT-irreversible reversible read/navigate step resumes
     with ``approve``; an **irreversible OR unknown** step records the hard-stop
     and resumes with ``reject`` (NEVER approves a real irreversible side-effect on
     a live third-party site).

The plan, every step, every done-check and the irreversibility classification are
LLM-derived + code-enforced — ZERO baked topology. The SAME driver runs Goal A
(read-only, usa.gov) and Goal B (a benign real form that exercises the gate).

Run:
  .venv/bin/python -m clarion.app.gov_proof                 # both goals, default sites
  .venv/bin/python -m clarion.app.gov_proof A               # Goal A only (usa.gov)
  .venv/bin/python -m clarion.app.gov_proof B               # Goal B only (the form)
  GOV_HEADLESS=0 …                                          # watch the browser
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_AGENT_ROOT, ".env"))

from clarion.app.runtime import HeroRuntime  # noqa: E402
from clarion.contracts.events import ConsentDecision, ConsentRequest  # noqa: E402
from clarion.contracts.ports import Reasoner  # noqa: E402
from clarion.contracts.state import (  # noqa: E402
    ConsentRecord,
    Fact,
    PageReadout,
    SelectorMap,
    StepProposal,
    Subgoal,
    WorkflowEpisode,
)
from clarion.stages.graph import seed_stage_state  # noqa: E402

HEADLESS = os.environ.get("GOV_HEADLESS", "1") != "0"

# The two real sites. Goal A is a read-only government lookup; Goal B is a BENIGN
# real form where merely REACHING the submit control is harmless (see the note in
# ``main``). Overridable by env, but the defaults are the proof sites.
GOAL_A_URL = os.environ.get("GOV_A_URL", "https://www.usa.gov/benefits")
GOAL_A_GOAL = os.environ.get(
    "GOV_A_GOAL", "find what government benefits I might be eligible for"
)
GOAL_B_URL = os.environ.get("GOV_B_URL", "https://www.weather.gov/")
GOAL_B_GOAL = os.environ.get(
    "GOV_B_GOAL", "submit the form to get the local weather forecast"
)


# ---------------------------------------------------------------------------
# Output helpers (a clean transcript — the proof artifact).
# ---------------------------------------------------------------------------


def _hr(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}", flush=True)


def _p(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# 503-aware Reasoner wrapper — retry/backoff on MiniMax high-demand, then fall
# over to a SECONDARY MiniMax client. The hard constraint: handle transient
# 503/high-demand; if it persists, fall back and SAY SO. This wraps a Reasoner
# transparently so the kernel/stage graph see the frozen port unchanged.
# ---------------------------------------------------------------------------


def _is_overloaded(exc: Exception) -> bool:
    """Does this look like a transient provider overload (503 / UNAVAILABLE /
    RESOURCE_EXHAUSTED / high-demand)? Conservative — only retry/failover on a
    transient signal, never on a real schema/guard error."""
    msg = str(exc).lower()
    needles = (
        "503", "500", "unavailable", "overloaded", "high demand", "high-demand",
        "resource_exhausted", "rate limit", "rate_limit", "try again", "timeout",
        "deadline", "internal error", "temporarily",
    )
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return status in (429, 500, 503) or any(n in msg for n in needles)


class ResilientReasoner(Reasoner):
    """A frozen-port ``Reasoner`` that wraps a PRIMARY (MiniMax-M3) and, on
    persistent transient overload, FAILS OVER to a SECONDARY (MiniMax). Each call:
    retry the primary with exponential backoff; if it still overloads, try the
    secondary
    (also with backoff); on a non-transient error, raise. Records every failover so
    the report can say plainly which provider answered.

    Construction is lazy/cheap — the secondary client is only built if the primary
    actually fails over."""

    def __init__(
        self,
        primary: Reasoner,
        *,
        secondary_factory: Optional[Any] = None,
        max_retries: int = 3,
        base_delay_s: float = 1.5,
    ) -> None:
        self._primary = primary
        self._secondary_factory = secondary_factory
        self._secondary: Optional[Reasoner] = None
        self._max_retries = max_retries
        self._base = base_delay_s
        # Observability: which provider served each call, and any failover events.
        self.events: list[str] = []
        self.served_by: list[str] = []
        # Mirror the primary's decide-latency surface (the report reads this).
        self.last_decide_ms: float | None = None

    @property
    def primary_name(self) -> str:
        return type(self._primary).__name__

    def _get_secondary(self) -> Optional[Reasoner]:
        if self._secondary is None and self._secondary_factory is not None:
            try:
                self._secondary = self._secondary_factory()
            except Exception as exc:  # noqa: BLE001
                self.events.append(f"secondary unavailable: {exc!r}")
                self._secondary = None
        return self._secondary

    async def _with_failover(self, method: str, *args: Any) -> Any:
        # (1) the primary, with backoff on transient overload.
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries):
            try:
                result = await getattr(self._primary, method)(*args)
                self.served_by.append(self.primary_name)
                self.last_decide_ms = getattr(self._primary, "last_decide_ms", None)
                return result
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not _is_overloaded(exc):
                    raise
                delay = self._base * (2 ** attempt)
                self.events.append(
                    f"{self.primary_name}.{method} overloaded "
                    f"(attempt {attempt + 1}/{self._max_retries}): {exc} "
                    f"→ backoff {delay:.1f}s"
                )
                await asyncio.sleep(delay)
        # (2) primary persistently overloaded → fail over to the secondary.
        secondary = self._get_secondary()
        if secondary is None:
            raise RuntimeError(
                f"{self.primary_name}.{method} persistently overloaded and no "
                f"failover reasoner available"
            ) from last_exc
        sec_name = type(secondary).__name__
        self.events.append(
            f"FAILOVER → {sec_name} after {self._max_retries} overloaded "
            f"{self.primary_name} attempts"
        )
        for attempt in range(self._max_retries):
            try:
                result = await getattr(secondary, method)(*args)
                self.served_by.append(sec_name)
                self.last_decide_ms = getattr(secondary, "last_decide_ms", None)
                return result
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not _is_overloaded(exc):
                    raise
                delay = self._base * (2 ** attempt)
                self.events.append(
                    f"{sec_name}.{method} overloaded "
                    f"(attempt {attempt + 1}/{self._max_retries}): {exc} "
                    f"→ backoff {delay:.1f}s"
                )
                await asyncio.sleep(delay)
        raise RuntimeError(
            f"both {self.primary_name} and {sec_name} persistently overloaded"
        ) from last_exc

    async def plan_goal(
        self, goal: str, orient: PageReadout, affordances: list[Fact]
    ) -> list[Subgoal]:
        return await self._with_failover("plan_goal", goal, orient, affordances)

    async def decide_step(
        self,
        goal: str,
        ranked_slice: SelectorMap,
        facts: list[Fact],
        history: list[StepProposal],
        context=None,  # noqa: ANN001 - forwarded verbatim to the wrapped reasoner
    ) -> StepProposal:
        return await self._with_failover(
            "decide_step", goal, ranked_slice, facts, history, context
        )


def _build_reasoner() -> ResilientReasoner:
    """The live decider: MinimaxReasoner (MiniMax-M3) primary, with a same-provider
    MiniMax failover behind the 503-aware wrapper. ``MINIMAX_LLM_MODEL_FALLBACK``
    lets the event point the secondary at a high-speed MiniMax model if M3 is
    saturated; absent it, the secondary is a fresh M3 client. Lazy clients (no I/O
    at construct)."""
    from clarion.adapters.minimax_reasoner import MinimaxReasoner

    def _secondary_factory():
        return MinimaxReasoner(model=os.environ.get("MINIMAX_LLM_MODEL_FALLBACK"))

    return ResilientReasoner(MinimaxReasoner(), secondary_factory=_secondary_factory)


# ---------------------------------------------------------------------------
# The proof record — what each run captured (the report substrate).
# ---------------------------------------------------------------------------


@dataclass
class StepRecord:
    node: str
    event: str
    data: dict


@dataclass
class ConsentEvent:
    proposal_id: str
    utterance: str
    irreversible: bool
    decision: str  # what the autonomous policy did: "approve" | "reject"
    reason: str


@dataclass
class ProofResult:
    goal: str
    url: str
    subgoals: list[Subgoal] = field(default_factory=list)
    plan_utterance: str = ""
    grounded_values: list[Fact] = field(default_factory=list)
    consent_events: list[ConsentEvent] = field(default_factory=list)
    hard_stops: int = 0
    approvals: int = 0
    trace: list[StepRecord] = field(default_factory=list)
    decide_ms: list[float] = field(default_factory=list)
    perceive_ms: list[float] = field(default_factory=list)
    gate_classifications: list[str] = field(default_factory=list)
    hedged: list[str] = field(default_factory=list)
    rejected_proposals: list[str] = field(default_factory=list)
    error: str = ""
    served_by: list[str] = field(default_factory=list)
    failover_events: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# User-memory projection — turn a finished ProofResult into the episode record
# (the knowledge-layer write-back). Pure helpers; never raise.
# ---------------------------------------------------------------------------


def _episode_outcome(res: ProofResult) -> str:
    """``completed`` (clean read-only finish) / ``declined`` (a hard-stop fired —
    a first-class success: the gate did its job) / ``error`` (a real crash; the
    consent-turn-bound case is the expected Goal-B reject loop, NOT an error)."""
    if res.error and "consent-turn bound" not in res.error:
        return "error"
    if res.hard_stops > 0:
        return "declined"
    return "completed"


def _mean(xs: list[float]) -> float:
    vals = [x for x in (xs or []) if x is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _remembered(c: "ConsentEvent") -> ConsentRecord:
    """ConsentEvent (app dataclass) → ConsentRecord (frozen value object)."""
    return ConsentRecord(
        proposal_id=c.proposal_id,
        utterance=c.utterance,
        irreversible=c.irreversible,
        decision=c.decision,
    )


# ---------------------------------------------------------------------------
# The GENERIC driver — runs ONE goal on ONE site, no site-specific logic.
# ---------------------------------------------------------------------------


class GovProofDriver:
    """Drives the de-hardcoded TAS for a single (goal, url) to completion across
    the parent consent ``interrupt()``, applying the autonomous consent policy and
    recording everything the proof needs. ZERO site-specific code."""

    # Bound the drive so a wedged page / replan loop can't run forever.
    _MAX_CONSENT_TURNS = 12

    def __init__(self, runtime: HeroRuntime, goal: str, url: str) -> None:
        self.rt = runtime
        self.goal = goal
        self.url = url
        self.result = ProofResult(goal=goal, url=url)

    def _consent_policy(self, req: ConsentRequest) -> tuple[str, str]:
        """SAFETY-CRITICAL autonomous decision over a surfaced ConsentRequest.

        Returns ``(decision, reason)``. The kernel's IrreversibilityGate has already
        run the dual-signal classifier and set ``req.irreversible`` (True for an
        irreversible OR unknown step). So:
          - ``irreversible == True``  → the hard-stop fired → REJECT (never approve a
            real irreversible side-effect on a live third-party site).
          - ``irreversible == False`` → a reversible read/navigate step → APPROVE to
            progress (this is the only side-effect class we let through).
        """
        if req.irreversible:
            return (
                "reject",
                "irreversible/unknown per the dual-signal gate — hard-stop; "
                "the driver declines and never executes the side-effect",
            )
        return (
            "approve",
            "classified reversible (read/navigate) — safe to progress autonomously",
        )

    async def _perceive_timed(self) -> SelectorMap:
        t0 = time.perf_counter()
        sm = await self.rt.actuator.perceive()
        self.result.perceive_ms.append((time.perf_counter() - t0) * 1000.0)
        return sm

    def _record_trace(self, trace: list) -> None:
        """Pull the interesting kernel/executor trace events into the proof record
        (gate classifications, hedges, rejected proposals, decide_ms)."""
        for e in trace:
            self.result.trace.append(StepRecord(node=e.node, event=e.event, data=dict(e.data)))
            d = e.data
            if e.node == "GATE" and "classification" in d:
                self.result.gate_classifications.append(str(d["classification"]))
            if e.node == "PROPOSE" and d.get("hedged"):
                self.result.hedged.append(str(d["hedged"]))
            if e.node == "PROPOSE" and d.get("rejected"):
                self.result.rejected_proposals.append(str(d["rejected"]))
            if e.node == "PROPOSE" and d.get("decide_ms") is not None:
                try:
                    self.result.decide_ms.append(float(d["decide_ms"]))
                except (TypeError, ValueError):
                    pass

    async def run(self) -> ProofResult:
        graph = self.rt.build_stage_graph()
        cfg = {"configurable": {"thread_id": f"gov-{abs(hash(self.url)) % 100000}"}}

        # Seed the executor with the freshly perceived live page (the planner reads
        # the ORIENT readout + affordances off the actuator; the kernel grounds on
        # the live page via PageRetriever).
        page = await self._perceive_timed()
        seed = seed_stage_state(goal=self.goal, mode=self.rt.mode, page_index=page)

        try:
            state = await graph.ainvoke(seed, cfg)
        except Exception as exc:  # noqa: BLE001
            self.result.error = f"{type(exc).__name__}: {exc}"
            _p(f"  [drive] ERROR during initial invoke: {self.result.error}")
            return self._finalize()

        # Drive across the parent consent interrupt(): each surfaced ConsentRequest
        # is decided by the autonomous policy, then resumed.
        turns = 0
        from langgraph.types import Command

        while "__interrupt__" in state and turns < self._MAX_CONSENT_TURNS:
            turns += 1
            (intr,) = state["__interrupt__"]
            req = ConsentRequest.model_validate(intr.value)
            decision, reason = self._consent_policy(req)
            self.result.consent_events.append(
                ConsentEvent(
                    proposal_id=req.proposal_id,
                    utterance=req.utterance,
                    irreversible=req.irreversible,
                    decision=decision,
                    reason=reason,
                )
            )
            _p(
                f"  [consent #{turns}] proposal={req.proposal_id} "
                f"irreversible={req.irreversible}"
            )
            _p(f"      readback: {req.utterance!r}")
            _p(f"      AUTONOMOUS DECISION → {decision.upper()}  ({reason})")
            if decision == "reject":
                self.result.hard_stops += 1
            else:
                self.result.approvals += 1
            try:
                state = await graph.ainvoke(
                    Command(resume=ConsentDecision(decision=decision).model_dump()),
                    cfg,
                )
            except Exception as exc:  # noqa: BLE001
                self.result.error = f"{type(exc).__name__}: {exc}"
                _p(f"  [drive] ERROR during resume: {self.result.error}")
                break

        if "__interrupt__" in state and turns >= self._MAX_CONSENT_TURNS:
            self.result.error = (
                f"hit the {self._MAX_CONSENT_TURNS}-consent-turn bound (likely a "
                f"reject->replan loop after the hard-stop — expected for Goal B)"
            )

        # Harvest the final state into the record.
        self._record_trace(state.get("trace", []))
        self.result.subgoals = list(state.get("subgoals", []) or [])
        self.result.grounded_values = list(state.get("grounded_facts", []) or [])
        # The spoken plan utterance the planner emitted (legibility beat).
        for e in state.get("trace", []):
            if e.node == "PLANNER" and e.data.get("utterance"):
                self.result.plan_utterance = str(e.data["utterance"])
                break

        # User-memory write-back (the knowledge layer): persist this finished run as
        # an EPISODE so the NEXT run on the same goal plans faster. Stores the plan
        # SHAPE + consent decisions + timings — NEVER grounded_values. Opt-in via
        # CLARION_MEMORY=1, fire-and-forget, and never fails the run (a memory miss
        # is logged, not raised). Skipped on a crashed (error) outcome.
        outcome = _episode_outcome(self.result)
        if (
            getattr(self.rt, "memory", None) is not None
            and os.environ.get("CLARION_MEMORY") == "1"
            and outcome != "error"
        ):
            episode = WorkflowEpisode(
                goal=self.goal,
                url_host=(urlparse(self.url).hostname or ""),
                subgoals=self.result.subgoals,
                plan_utterance=self.result.plan_utterance,
                outcome=outcome,  # type: ignore[arg-type]
                consent=[_remembered(c) for c in self.result.consent_events],
                hard_stops=self.result.hard_stops,
                approvals=self.result.approvals,
                decide_ms_mean=_mean(self.result.decide_ms),
                perceive_ms_mean=_mean(self.result.perceive_ms),
                completed_at=time.time(),
            )
            # Same workflow-bar as the live offer: only record a real workflow
            # (transactional / multi-step / form), never a trivial read — so the
            # autonomous driver doesn't pollute the shared store with one-step reads.
            if not episode.is_workflow():
                _p("  [memory] episode skipped (not a workflow: trivial read)")
            else:
                try:
                    await self.rt.memory.write_episode(self.rt.user_id, episode)
                    _p(f"  [memory] episode saved (outcome={outcome})")
                except Exception as exc:  # noqa: BLE001 — never fail a run on a memory miss.
                    _p(f"  [memory] episode write skipped: {exc}")

        return self._finalize()

    def _finalize(self) -> ProofResult:
        r = self.rt.reasoner
        self.result.served_by = list(getattr(r, "served_by", []) or [])
        self.result.failover_events = list(getattr(r, "events", []) or [])
        return self.result


# ---------------------------------------------------------------------------
# Report rendering — the headline transcript per goal.
# ---------------------------------------------------------------------------


def _print_report(label: str, res: ProofResult) -> None:
    _hr(f"{label} TRANSCRIPT — goal {res.goal!r} on {res.url}")

    _p("GOAL-DERIVED PLAN (subgoals reasoned from the goal + ORIENT readout; "
       "no baked stage names):")
    if res.subgoals:
        for i, s in enumerate(res.subgoals):
            _p(f"  subgoal {i}: {s.description!r}   done_check={s.done_check!r}")
    else:
        _p("  (no subgoals — planner returned empty / failed-open)")
    if res.plan_utterance:
        _p(f"  spoken plan (legibility): {res.plan_utterance!r}")

    _p("\nGROUNDED VALUES READ BACK (each with a real source_node_id citation):")
    spoken = [f for f in res.grounded_values if f.source_node_id and f.verified]
    if spoken:
        for f in spoken[:8]:
            crisp = f.value if len(f.value) <= 90 else f.value[:87] + "…"
            _p(f"  value={crisp!r}")
            _p(f"      ↳ citation source_node_id={f.source_node_id!r} "
               f"polarity={f.polarity} verified={f.verified}")
    else:
        _p("  (no grounded+verified value in final state — see trace)")
    ungrounded = [f for f in res.grounded_values if not f.source_node_id]
    if ungrounded:
        _p(f"  [membership fence] {len(ungrounded)} ungrounded fact(s) present in "
           f"state but UNSPEAKABLE (source_node_id=None → refused by VERIFY).")

    _p("\nIRREVERSIBILITY GATE classifications (dual-signal, per consequential step):")
    if res.gate_classifications:
        for c in res.gate_classifications:
            _p(f"  classified: {c}")
    else:
        _p("  (no consequential step reached the gate — read-only auto-proceeded)")

    _p("\nCONSENT (autonomous policy decisions):")
    if res.consent_events:
        for c in res.consent_events:
            _p(f"  proposal={c.proposal_id} irreversible={c.irreversible} "
               f"→ {c.decision.upper()}")
            _p(f"      readback: {c.utterance!r}")
            _p(f"      reason: {c.reason}")
    else:
        _p("  (no consent interrupt surfaced — read-only run, no consequential act)")
    _p(f"  hard-stops (declined): {res.hard_stops}   approvals (reversible "
       f"progressed): {res.approvals}")

    if res.hedged:
        _p("\nNEGATIVE-VERIFIER HEDGES (an uncovered negative downgraded, never a "
           "confident negative):")
        for h in res.hedged:
            _p(f"  hedged: {h}")
    if res.rejected_proposals:
        _p("\nREASONER-GUARD REJECTS (off-page index / dangling value_ref → safe "
           "read-back, never acted):")
        for rj in res.rejected_proposals:
            _p(f"  rejected: {rj}")

    # Anchor certification — the done-check that certified a subgoal via the
    # semantic anchor (URL before/after) lives on the EXECUTOR exit trace.
    _p("\nDONE-CHECK / ANCHOR CERTIFICATION (executor exits — the generic check "
       "evaluated in CODE against the re-perceived tree + URL anchor):")
    exec_exits = [t for t in res.trace if t.node == "EXECUTOR" and t.event == "exit"]
    if exec_exits:
        for t in exec_exits:
            d = t.data
            _p(f"  subgoal={d.get('subgoal')} done={d.get('done')} "
               f"check={d.get('success_check')!r}  "
               f"url_before={d.get('url_before')!r} → url_after={d.get('url_after')!r}")
    else:
        _p("  (no executor exit recorded)")

    _p("\nPER-STEP TIMING:")
    if res.perceive_ms:
        _p(f"  perceive_ms: {[round(x) for x in res.perceive_ms]} "
           f"(mean {sum(res.perceive_ms)/len(res.perceive_ms):.0f}ms)")
    if res.decide_ms:
        _p(f"  decide_ms:   {[round(x) for x in res.decide_ms]} "
           f"(mean {sum(res.decide_ms)/len(res.decide_ms):.0f}ms) "
           f"— ~2s expected (thinking_budget=0, no Step-6 speculation)")
    _p(f"  reasoner served by: {res.served_by or ['(none)']}")
    if res.failover_events:
        _p("  PROVIDER EVENTS (503 backoff / failover):")
        for ev in res.failover_events:
            _p(f"    {ev}")
    if res.error:
        _p(f"\n  NOTE: {res.error}")


# ---------------------------------------------------------------------------
# Run one goal end-to-end (build runtime, drive, report, close).
# ---------------------------------------------------------------------------


async def run_goal(label: str, goal: str, url: str) -> ProofResult:
    _hr(f"{label}: driving the de-hardcoded TAS on a REAL site (mode=normal)\n"
        f"  url:  {url}\n  goal: {goal!r}")
    reasoner = _build_reasoner()
    rt = await HeroRuntime.create(
        url, mode="normal", headless=HEADLESS, reasoner=reasoner
    )
    _p(f"  actuator: {type(rt.actuator).__name__}   "
       f"reasoner: ResilientReasoner(primary={reasoner.primary_name})   "
       f"retriever: {type(rt.retriever).__name__}(PageRetriever)")
    try:
        driver = GovProofDriver(rt, goal, url)
        res = await driver.run()
    finally:
        await rt.close()
    _print_report(label, res)
    return res


def _verdict(a: Optional[ProofResult], b: Optional[ProofResult]) -> None:
    _hr("HEADLINE VERDICT — does TAS drive a full goal end-to-end on a real gov "
        "site, ZERO site-specific code, every invariant enforced?")

    def ck(cond: bool) -> str:
        return "PASS" if cond else "FAIL"

    if a is not None:
        a_plan = bool(a.subgoals)
        a_grounded = any(f.source_node_id and f.verified for f in a.grounded_values)
        a_no_crash = not a.error or "consent-turn bound" in a.error
        _p(f"Goal A (read-only, {a.url}):")
        _p(f"  [{ck(a_plan)}] goal-derived plan stated ({len(a.subgoals)} subgoals)")
        _p(f"  [{ck(a_grounded)}] grounded value read back with a real citation")
        _p(f"  [{ck(a_no_crash)}] drove to completion (or honestly declined)")
    if b is not None:
        b_gate = any(c in ("irreversible", "unknown") for c in b.gate_classifications) \
            or any(e.irreversible for e in b.consent_events)
        b_hardstop = b.hard_stops > 0
        b_never_submitted = b.hard_stops > 0 and all(
            e.decision == "reject" for e in b.consent_events if e.irreversible
        )
        _p(f"\nGoal B (the form site, {b.url}):")
        _p(f"  [{ck(b_gate)}] dual-signal gate classified the consequential control "
           f"irreversible/unknown")
        _p(f"  [{ck(b_hardstop)}] CONSENT hard-stop fired")
        _p(f"  [{ck(b_never_submitted)}] driver DECLINED — never submitted")

    a_ok = a is not None and bool(a.subgoals) and any(
        f.source_node_id and f.verified for f in a.grounded_values
    )
    b_ok = b is not None and b.hard_stops > 0
    if a is not None and b is not None:
        overall = a_ok and b_ok
        _p(f"\n>>> HEADLINE: {'YES' if overall else 'PARTIAL/NO'} — "
           f"{'TAS drove both goals on real sites, zero site-specific code, gate + grounding enforced.' if overall else 'see per-goal checks above.'}")


async def main() -> int:
    which = (sys.argv[1].upper() if len(sys.argv) > 1 else "AB")

    _hr("CLARION AG-PROVE — generic autonomous driver over the de-hardcoded TAS")
    _p("Real keys (agent/.env), real sites, autonomous Playwright (headless="
       f"{HEADLESS}). Behavioral proof, not an exit code.")
    _p("\nSite safety note for Goal B (weather.gov): a public GOV forecast form. Its "
       "'Get Weather' control submits a <form> that navigates to a forecast page — "
       "completely harmless (no account, no payment, no irreversible side-effect), "
       "and the page has NO undo/cancel affordance, so the structural net escalates "
       "the consequential control to 'unknown' even if the model judges it benign. "
       "The proof is that the dual-signal gate STOPS at that control and the driver "
       "DECLINES; we never cross it.")

    a_res: Optional[ProofResult] = None
    b_res: Optional[ProofResult] = None

    if "A" in which:
        a_res = await run_goal("GOAL A", GOAL_A_GOAL, GOAL_A_URL)
    if "B" in which:
        b_res = await run_goal("GOAL B", GOAL_B_GOAL, GOAL_B_URL)

    _verdict(a_res, b_res)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
