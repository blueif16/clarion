"""ST1 → the GENERIC EXECUTOR (architecture migration Step 3): the goal-derived
plan + the kernel loop per subgoal, over the shared state.

**Not a baked AUTH→…→CONFIRM topology.** The hardcoded 6-stage pay-plan is
DELETED. The planner asks the injected ``Reasoner`` for a goal-derived
``list[Subgoal]`` (from the goal + the ORIENT readout + the page affordances);
the single generic ``executor`` node runs the K1 kernel loop scoped to the
current subgoal over the SHARED ``ClarionState``, then advances on the GENERIC
done-check — the reasoner-SELECTED ``success_check`` evaluated in CODE by
``stages.checks.evaluate_success_check`` (killer-closer #3, never model say-so).

KEPT verbatim: the RESCUE cross-cut (``detect_rescue`` — the most-validated
trigger), the bounded ``replanner``, and the §18.7 content-keyed reducer dedup
for the kernel sub-loop that re-executes across a consent interrupt.

Built ON K1 (imports the kernel + policy read-only; never edits them) and the
FROZEN ``clarion.contracts``. Private control-flow channels live on the
``_PlanState`` superset (the kernel's, extended) so ``contracts/`` stays frozen.

This module OWNS only ``clarion/stages/``; it imports kernel + contracts read-only.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Awaitable, Callable, Literal, Optional

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.constants import END, START
from langgraph.graph import StateGraph
from langgraph.types import Command, interrupt

from urllib.parse import urlparse

from clarion.actuator.pipeline import readout_from_selector_map
from clarion.contracts.events import ConsentDecision, ConsentRequest
from clarion.contracts.ports import Actuator, ContextRanker, Memory, Reasoner, Retriever
from clarion.contracts.state import (
    ClarionState,
    Consent,
    ConsentRecord,
    Fact,
    PageReadout,
    PairedFact,
    SelectorMap,
    Subgoal,
    TraceEvent,
    WorkflowEpisode,
)
from clarion.kernel.graph import _ALLOWED_MSGPACK_MODULES, _PlanState, build_kernel, seed_state
from clarion.stages.checks import evaluate_success_check, make_anchor
from clarion.stages.planner import plan_goal, verbalize_subgoals
from clarion.stages.predicates import detect_rescue, needs_rescue


# ---------------------------------------------------------------------------
# Private control-flow schema — the kernel's _PlanState superset, plus this
# graph's leading-underscore control-flow channels (contracts stay frozen).
# ---------------------------------------------------------------------------


class _StageState(_PlanState, total=False):
    """The executor graph's runtime schema: the kernel ``_PlanState`` superset
    (frozen ``ClarionState`` + the additive plan/reasoner keys) PLUS this graph's
    private, leading-underscore control-flow channels (rescue-return target,
    replan bookkeeping, the before-map anchor for the done-check). ``total=False``
    so a bare ``ClarionState`` seed is still valid input."""

    # The executor node to return to after the rescue sub-flow completes.
    _rescue_return: Optional[str]
    # The subgoal index rescue last resolved for → won't re-trigger on same tree.
    _rescue_done_for: Optional[int]
    # How many replans we've spent on the current subgoal (bounded).
    _replan_attempts: int
    # Per-subgoal inner-kernel thread ids (subgoal idx → kernel thread_id), so a
    # consent interrupt surfaced through the parent resumes on the SAME inner
    # thread after the parent resumes.
    _kernel_threads: dict[str, str]
    # A per-run nonce (set once at seed) that namespaces the inner-kernel thread ids.
    # The inner thread id is derived deterministically from (_run_id, subgoal idx,
    # replan attempt) so a consent interrupt + resume of the SAME attempt RESUMES the
    # parked child instead of re-seeding + re-deciding it (the wasted ~3s decode on
    # every "yes"), while a replan / a fresh goal still gets a clean child thread.
    _run_id: str
    # The SelectorMap BEFORE the current subgoal's kernel ran (the done-check diff
    # baseline) and the URL/anchor at that point.
    _before_map: Optional[SelectorMap]
    _anchor: Optional[str]
    # The recalled past consent decisions (the user-memory reuse hook) — surfaced as
    # an advisory spoken reminder at the gate; NEVER auto-consents.
    consent_recall: list[ConsentRecord]
    # (node_id -> typed value) of the fields filled during the run — the end-of-flow
    # "remember?" offer's candidate source (knowledge-layer #4c). Accumulated by the
    # executor across subgoals/replans; consumed by the ``remember`` node.
    _filled: dict[str, str]


_PLANNER = "planner"
_EXECUTOR = "executor"
_RESCUE = "rescue"
_REPLANNER = "replanner"
_REMEMBER = "remember"
_SAVE_WORKFLOW = "save_workflow"


def _trace(node: str, event: str = "info", **data: object) -> TraceEvent:
    return TraceEvent(node=node, event=event, at=time.time(), data=dict(data))


def _trace_key(e: TraceEvent) -> tuple:
    """Content identity of a trace event, for idempotent forwarding (a re-executed
    node must not re-append an event the parent channel already holds)."""
    return (e.node, e.event, e.at, repr(sorted(e.data.items())))


def _consent_key(c: Consent) -> tuple:
    return (c.proposal_id, c.decision, c.value, c.at)


def _host_of(url: Optional[str]) -> str:
    """The registrable host of a URL (the episode recall scope key). Best-effort —
    a blank/garbage URL just yields ``""`` (recall is by goal semantics anyway)."""
    try:
        return (urlparse(url or "").hostname or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def make_stage_checkpointer() -> InMemorySaver:
    """Same contract-model allowlist as the kernel (execution §18.6) so the
    executor graph's checkpointed state round-trips warning-free."""
    return InMemorySaver(
        serde=JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MSGPACK_MODULES)
    )


# ---------------------------------------------------------------------------
# ORIENT helpers — gather the readout + affordances + pairings the planner /
# kernel need, degrading gracefully on an actuator without the extra reads.
# ---------------------------------------------------------------------------


async def _orient(actuator: Actuator, sm: SelectorMap) -> PageReadout:
    """The ORIENT readout for the planner. Prefers the actuator's full-AXTree
    ``describe_page``; falls back to the interactive ``SelectorMap`` so a fake /
    cached transport still yields a grounded (if thinner) readout."""
    describe = getattr(actuator, "describe_page", None)
    if describe is not None:
        return await describe()
    return readout_from_selector_map(sm)


async def _paired_facts(actuator: Actuator) -> list[PairedFact]:
    """The geometric label↔value pairings THIS cycle (fence #3 supply). Empty for
    an actuator without ``read_paired_facts`` (the fakes) — the pairing fence then
    simply has nothing to back an "X is Y" claim, which is the safe default."""
    read = getattr(actuator, "read_paired_facts", None)
    if read is None:
        return []
    return await read()


# An injected, best-effort consult of the per-site STRUCTURE index (knowledge-layer
# #4(a)). Takes (url, goal) → grounded structure Facts of OTHER pages on the site;
# `None` (default) keeps the page-only planner. Duck-typed so `stages/` imports no
# provider/app code — the real impl (`app.site_indexer.SiteKnowledge`) is injected
# by the runtime. Always fail-open: a miss returns [] and the planner degrades.
SiteContext = Callable[[str, str], Awaitable[list[Fact]]]

# An injected, fire-and-forget hook handed the live ORIENT url at PLAN time — the app
# layer SCHEDULES a background public-structure crawl of the host (the knowledge-layer
# AUTO-INDEX trigger). Returns quickly, never raises; `None` (default) disables it.
# Duck-typed so `stages/` imports zero app code (mirrors `SiteContext`); the real impl
# is `app.auto_index.schedule_auto_index`.
OrientHook = Callable[[str], object]

# The end-of-flow "remember?" nominator (knowledge-layer #4c). Takes (filled,
# page) → reusable ``(key, value)`` preference candidates with SECRETS ALREADY
# SUPPRESSED. ``None`` (default) disables the offer entirely. Injected by the
# runtime — duck-typed so ``stages/`` imports no ``app/`` code (the real impl wraps
# ``app.remember.nominate_remember_candidates``, mirroring ``SiteContext``).
RememberNominate = Callable[[dict[str, str], SelectorMap], list[tuple[str, str]]]


def _remember_offer_utterance(candidates: list[tuple[str, str]]) -> str:
    """Batch the kept candidates into ONE spoken offer (persona: in-command, no
    banned words). Ends on an explicit yes/no so the consent is unambiguous."""
    items = "; ".join(f"{k} as {v}" for k, v in candidates)
    return (
        f"Before we finish — I can remember {items} for next time. "
        f"Say yes to keep, or no to forget."
    )


def _save_workflow_offer_utterance(goal: str, host: str) -> str:
    """The ONE spoken "save this workflow?" offer (persona: in-command, no banned
    words). Ends on an explicit yes/no so the consent is unambiguous."""
    where = f" on {host}" if host else ""
    return (
        f"You just finished {goal}{where}. I can remember these steps as a workflow "
        f"so next time is faster. Say yes to save, or no to skip."
    )


def _consent_records_from_trace(trace: list[TraceEvent]) -> list[ConsentRecord]:
    """Reconstruct the run's ConsentRecords from the kernel CONSENT exit events the
    executor forwarded up (the kernel ``Consent`` log carries no ``irreversible`` /
    ``utterance``). Deduped by ``proposal_id`` (a consent re-executes across an
    interrupt resume; keep the last decision) so counts aren't inflated."""
    by_id: dict[str, ConsentRecord] = {}
    for e in trace:
        if e.node != "CONSENT" or e.event != "exit":
            continue
        pid = str(e.data.get("proposal_id") or "")
        by_id[pid] = ConsentRecord(
            proposal_id=pid,
            utterance=str(e.data.get("utterance") or ""),
            irreversible=bool(e.data.get("irreversible", False)),
            decision=str(e.data.get("decision") or ""),
        )
    return list(by_id.values())


def _episode_from_state(state: dict, host: str) -> WorkflowEpisode:
    """Project a COMPLETED run's shared state into a ``WorkflowEpisode`` (the live
    counterpart of ``gov_proof``'s ``ProofResult`` harvest). Stores the plan SHAPE +
    consent + the filled-field count — NEVER a grounded value."""
    subgoals: list[Subgoal] = list(state.get("subgoals", []) or [])
    consent = _consent_records_from_trace(state.get("trace", []) or [])
    return WorkflowEpisode(
        goal=state.get("goal", ""),
        url_host=host,
        subgoals=subgoals,
        plan_utterance=verbalize_subgoals(subgoals),
        outcome="completed",  # the save node is reached only on a clean finish.
        consent=consent,
        approvals=sum(1 for c in consent if c.decision == "approve"),
        hard_stops=sum(1 for c in consent if c.decision == "reject"),
        n_filled=len(state.get("_filled") or {}),
        completed_at=time.time(),
    )


def _with_site_map(orient: PageReadout, site_facts: list[Fact]) -> PageReadout:
    """Return a COPY of the ORIENT readout whose summary carries a SITE MAP block
    built from the per-site structure facts — for PLANNING only (which page to
    navigate to). These are cross-page structure, NOT live current-page values, so
    they never enter GROUND; the copy keeps the spoken readback path untouched.

    The candidates are framed as MAY-NOT-CONTAIN-THE-TARGET on purpose: the
    structure index is a vector retriever whose score is a normalized RANK, not a
    calibrated relevance (a goal whose destination was never indexed still returns
    the nearest page at a top-rank score — verified empirically), so a numeric
    threshold cannot tell "indexed" from "absent." The reliable signal is the
    RANK (the right page sorts first WHEN present); the absent case is rejected
    SEMANTICALLY by the Reasoner — the LLM already in the loop — not by a cutoff.
    Hence the instruction below: match a candidate only if it CLEARLY fits the
    destination the user named, else fall back to the site's own search instead of
    navigating to a best-guess page (the epistemic invariant at the nav layer:
    say you can't find it rather than guess). Pure NL steering — no keyword list."""
    lines = "\n".join(
        f"  - {f.value.replace(chr(10), ' · ')[:200]}" for f in site_facts
    )
    extra = (
        "\n\nSITE MAP (candidate pages from PRIOR structure indexing — NOT grounded "
        "current-page values, and NOT guaranteed to include the destination the user "
        "asked for). Use a candidate ONLY if it CLEARLY matches that destination. If "
        "none clearly matches, do NOT navigate to a best-guess page — instead use "
        "this site's own search control (if the page affords one) to look up the "
        "exact destination, telling the user you're searching because no known page "
        "matched; if there is no search control, say you could not find it:\n" + lines
    )
    return orient.model_copy(update={"summary": orient.summary + extra})


async def _current_url(actuator: Actuator) -> Optional[str]:
    """The live page URL — the SEMANTIC ANCHOR substrate for the ``navigated``
    done-check. Both real actuators surface it on their ``describe_page`` readout
    (``PageReadout.url``: Playwright's ``page.url`` / the extension's
    ``location.href``); a fake/replay transport without ``describe_page`` yields
    ``None``, so ``navigated`` cleanly falls back to a structural delta. Best-effort
    — never lets a URL read break the done-check (a blocked ``location.href`` just
    degrades the anchor)."""
    describe = getattr(actuator, "describe_page", None)
    if describe is None:
        return None
    try:
        readout = await describe()
    except Exception:  # noqa: BLE001 - the anchor is best-effort; degrade, don't crash.
        return None
    url = (readout.url or "").strip()
    return url or None


def build_stage_graph(
    reasoner: Reasoner,
    retriever: Retriever,
    actuator: Actuator,
    *,
    mode: Literal["normal", "fast"] = "normal",
    max_replans: int = 2,
    site_context: Optional[SiteContext] = None,
    on_orient: Optional[OrientHook] = None,
    memory: Optional[Memory] = None,
    user_id: str = "default",
    remember_nominate: Optional[RememberNominate] = None,
    offer_workflow_save: bool = False,
    ranker: Optional[ContextRanker] = None,
    rank_min_nodes: Optional[int] = None,
):
    """Compile the GENERIC EXECUTOR graph.

    Topology::

        START → planner → executor ⇄ (replanner) → END
                            │
                            └→ rescue → back to executor

    - ``planner``  — asks the ``Reasoner`` for a goal-derived ``list[Subgoal]``
                     (from goal + ORIENT readout + affordances), speaks the plan
                     (legibility), routes to the executor at subgoal 0.
    - ``executor`` — runs the K1 kernel loop scoped to the CURRENT subgoal over the
                     shared state (re-surfacing any consent interrupt through the
                     parent), then advances on the GENERIC done-check
                     (``evaluate_success_check`` against the re-perceived tree).
                     Advances subgoal idx on success; → replanner on failure.
    - ``rescue``   — KEPT: a choked-widget cross-cut (``detect_rescue``).
    - ``replanner``— KEPT: bounded retry of the current subgoal, then gives up.

    ``mode`` is threaded into the per-subgoal kernel so the dual-signal gate
    behaves per foundation §5. NO baked stage names anywhere — the plan is whatever
    the reasoner derives from the goal.
    """

    # One kernel, reused across subgoals (state lives in ClarionState). The optional
    # semantic ``ContextRanker`` slices the candidate set PROPOSE hands the Reasoner
    # (gated to large pages by ``rank_min_nodes`` so it is win-or-free).
    kernel = build_kernel(
        reasoner,
        retriever,
        actuator,
        mode=mode,
        ranker=ranker,
        rank_min_nodes=rank_min_nodes,
    )

    # ---- planner ---------------------------------------------------------
    async def planner(state: _StageState) -> Command:
        """Derive the goal-derived plan and route to the executor at subgoal 0.

        When a ``site_context`` consult is injected, the planner first pulls the
        per-site STRUCTURE map (other pages + affordances, from prior indexing) and
        folds it into a COPY of the ORIENT readout so the Reasoner can plan WHICH
        page to navigate to — knowledge-layer #4(a). Best-effort: a miss (no index
        yet / no creds) yields no site facts and the planner runs page-only."""
        sm = state["page_index"]
        orient = await _orient(actuator, sm)
        # Knowledge-layer AUTO-INDEX trigger: hand the REAL current-page url to the
        # injected fire-and-forget hook (the app layer schedules a background public
        # structure crawl of this host). Guarded so a hook error never touches
        # planning; gated + throttled app-side.
        if on_orient is not None:
            try:
                on_orient(orient.url)
            except Exception:  # noqa: BLE001 - the warm-up hook is best-effort.
                pass
        plan_orient = orient
        n_site_facts = 0
        if site_context is not None:
            try:
                site_facts = await site_context(orient.url, state["goal"])
            except Exception:  # noqa: BLE001 - consult is optional; never break planning
                site_facts = []
            if site_facts:
                n_site_facts = len(site_facts)
                plan_orient = _with_site_map(orient, site_facts)

        # Knowledge-layer #4(c): RECALL the nearest past EPISODE on this goal to
        # warm-start the plan (the user-memory reuse hook). Advisory ONLY — the hint
        # rides into ``plan_goal`` as ``prior_plan_hint`` and the Reasoner re-grounds
        # against the live page; a remembered value is never spoken without being
        # re-grounded (``recall`` returns a ``Recall``, never a ``Fact``). Opt-in
        # (live) via CLARION_MEMORY=1; fail-open so recall never breaks planning.
        recall = None
        if memory is not None and os.environ.get("CLARION_MEMORY") == "1":
            try:
                recall = await memory.recall(user_id, state["goal"], _host_of(orient.url))
            except Exception:  # noqa: BLE001 — recall is advisory; never break planning.
                recall = None
        prior_plan_hint = recall.plan_hint if recall else None

        t_plan = time.time()
        subgoals = await plan_goal(
            reasoner,
            state["goal"],
            plan_orient,
            list(orient.affordances),
            prior_plan_hint=prior_plan_hint,
        )
        # The planner LLM decode — one of the two serial decodes (plan then decide)
        # that dominate the pre-consent wait, and the only one that was UNTIMED.
        plan_ms = (time.time() - t_plan) * 1000.0
        update: dict = {
            "subgoals": subgoals,
            "stage_idx": 0,
            "trace": [
                _trace(
                    "PLANNER",
                    "exit",
                    n_subgoals=len(subgoals),
                    n_site_facts=n_site_facts,
                    recalled=bool(prior_plan_hint),
                    plan=[s.description for s in subgoals],
                    plan_ms=plan_ms,
                    utterance=verbalize_subgoals(subgoals),
                )
            ],
        }
        # Stash the recalled consent decisions for the gate's spoken reminder.
        if recall is not None and recall.consent_recall:
            update["consent_recall"] = list(recall.consent_recall)
        # Surface the recalled prior plan as an advisory hint into DecideContext
        # (never binding — the reasoner re-grounds on the live page).
        if prior_plan_hint is not None and prior_plan_hint.subgoals:
            update["recall_hint"] = "; ".join(
                s.description for s in prior_plan_hint.subgoals if s.description
            )
        return Command(update=update, goto=_EXECUTOR)

    async def _drive_kernel(state: _StageState, subgoal: Subgoal, idx: int) -> dict:
        """Run the K1 kernel loop for ONE subgoal over the shared state, driving it
        to completion even across a consent interrupt (re-surfaced through the
        parent's own ``interrupt()`` so the voice plane / panel see the identical
        ``ConsentRequest``). The inner thread id persists in ``_kernel_threads`` so
        a parent resume reaches the same checkpoint."""
        threads = dict(state.get("_kernel_threads") or {})
        key = str(idx)
        # DETERMINISTIC inner-kernel thread id (the consent-resume latency fix). A
        # random uuid here regenerated on every parent re-execution, and
        # ``_kernel_threads`` never persists across a consent interrupt (the node
        # raises inside ``interrupt()`` before it can return the dict), so on a resume
        # the parked child was never found → ``child_parked`` was False → the kernel
        # re-seeded and RE-DECIDED the already-approved step (a wasted ~3s decode).
        # Deriving the id from (per-run nonce, subgoal idx, replan attempt) makes it
        # STABLE across one attempt's interrupt+resume (→ resume the parked child) yet
        # FRESH per replan (attempt++) and per goal (a new ``_run_id``), so no stale
        # ended-thread is ever re-seeded.
        run_id = str(state.get("_run_id") or "run")
        attempt = int(state.get("_replan_attempts", 0) or 0)
        thread_id = f"kernel-{run_id}-{idx}-a{attempt}"
        threads[key] = thread_id
        kernel_cfg = {"configurable": {"thread_id": thread_id}}

        # Seed the kernel with the frozen + additive plan channels scoped to THIS
        # subgoal's sub-goal. Pass the live paired_facts (the fence #3 supply).
        seed: _PlanState = {
            "goal": subgoal.description or state["goal"],
            # The user's VERBATIM intent for the whole task (the stage-level goal),
            # threaded so PROPOSE's DecideContext carries it un-genericized.
            "user_intent": state.get("goal", ""),
            "mode": state["mode"],
            "plan": state.get("plan", []),
            # The full plan + phase + trajectory + replan signal the step-decider
            # reasons inside (the rich-context build).
            "subgoals": state.get("subgoals", []),
            "step_history": state.get("step_history", []),
            "last_outcome": state.get("last_outcome", ""),
            "recall_hint": state.get("recall_hint", ""),
            "stage_idx": idx,
            "step": state["step"],
            # The replan attempt namespaces the kernel's per-step proposal_id so a
            # SUCCESSFUL-but-ineffective act in one attempt does not block (via the
            # carried-trace §2.3 once-flag) the DIFFERENT action the next replan
            # decides for this subgoal. Same value the inner-kernel thread id uses.
            "replan_attempt": attempt,
            "page_index": state["page_index"],
            "grounded_facts": state["grounded_facts"],
            "pending_proposal": state["pending_proposal"],
            "consent_log": list(state["consent_log"]),
            "trace": list(state["trace"]),
            "pending_step": state.get("pending_step"),
            "paired_facts": state.get("paired_facts", []),
        }  # type: ignore[assignment]

        # On a CONSENT RESUME the parent `executor` node re-executes from the TOP
        # (langgraph semantics), so this code re-runs. If the child kernel thread is
        # already parked at its consent interrupt, a bare `ainvoke(seed)` would
        # DISCARD that parked interrupt and re-decide from scratch — losing the
        # proposal the user just approved (ACT then never runs the approved click,
        # and the flow loops re-asking for consent). So: if the child is parked,
        # RESUME it with the cached decision instead of re-seeding.
        snap = await kernel.aget_state(kernel_cfg)
        child_parked = bool(snap.next)
        print(
            f"  [drive] subgoal={idx} {'RESUME parked kernel' if child_parked else 'seed fresh kernel'} "
            f"pending={(state.get('pending_proposal').id if state.get('pending_proposal') else None)}",
            flush=True,
        )
        if child_parked:
            pp = state.get("pending_proposal")
            # This interrupt() returns the ALREADY-cached consent decision (its value
            # is never re-surfaced), so it just hands the user's yes/no to the parked
            # child to drive it CONSENT → ACT exactly once on the approved proposal.
            req = ConsentRequest(
                proposal_id=pp.id if pp else "",
                utterance=pp.utterance if pp else "",
                irreversible=bool(pp.irreversible) if pp else True,
            )
            decision = ConsentDecision.model_validate(interrupt(req.model_dump()))
            result = await kernel.ainvoke(
                Command(resume=decision.model_dump()), kernel_cfg
            )
        else:
            result = await kernel.ainvoke(seed, kernel_cfg)
        for _ in range(8):
            if "__interrupt__" not in result:
                break
            (parent_intr,) = result["__interrupt__"]
            decision_payload = interrupt(parent_intr.value)
            decision = ConsentDecision.model_validate(decision_payload)
            result = await kernel.ainvoke(
                Command(resume=decision.model_dump()), kernel_cfg
            )
        result["_kernel_threads"] = threads  # type: ignore[index]
        return result

    # ---- executor --------------------------------------------------------
    async def executor(state: _StageState) -> Command:
        idx = int(state.get("stage_idx", 0) or 0)
        subgoals: list[Subgoal] = state.get("subgoals", []) or []
        if idx >= len(subgoals):
            return Command(goto=END)  # plan exhausted.
        subgoal = subgoals[idx]

        # (1) RESCUE cross-cut FIRST (KEPT): a choked widget in the tree we're
        #     about to act on → branch to rescue, come back here. Don't re-trigger
        #     if rescue just resolved for this subgoal.
        sm: SelectorMap = state["page_index"]
        if needs_rescue(sm) and state.get("_rescue_done_for") != idx:
            choked = detect_rescue(sm)
            return Command(
                update={
                    "_rescue_return": _EXECUTOR,
                    "_rescue_done_for": idx,
                    "trace": [
                        _trace(
                            "EXECUTOR",
                            "info",
                            subgoal=idx,
                            rescue_triggered=True,
                            choked_indices=[n.index for n in choked],
                        )
                    ],
                },
                goto=_RESCUE,
            )

        # Capture the before-map + the before-URL (the SEMANTIC ANCHOR baseline)
        # + harvest the live pairings for THIS cycle (the done-check diff baseline
        # + the fence #3 supply).
        before_map = sm
        before_url = await _current_url(actuator)
        paired = await _paired_facts(actuator)

        # (2) Run the kernel loop scoped to this subgoal over the shared state.
        merged = await _drive_kernel(
            {**state, "paired_facts": paired},  # type: ignore[arg-type]
            subgoal,
            idx,
        )

        # (3) GENERIC done-check (killer-closer #3): the reasoner-SELECTED
        #     success_check, evaluated in CODE against the re-perceived tree + the
        #     SEMANTIC ANCHOR (the URL before/after the act). The anchor lets
        #     ``navigated`` certify a real page move (URL changed) instead of a
        #     bare structural delta a benign SPA re-render also produces.
        fresh: SelectorMap = merged["page_index"]
        check_name = merged.get("success_check") or subgoal.done_check or ""
        after_url = await _current_url(actuator)
        # Knowledge-layer AUTO-INDEX (greedy, public-only): every NEW page the agent
        # navigates to gets the same background read-only PUBLIC crawl as the first
        # ORIENT, so the structure map fills in as we browse. Fire only on a real URL
        # change; the hook is gated/throttled/cookie-less app-side — private pages are
        # unreachable by the cookie-less crawler, so they're simply never indexed.
        if on_orient is not None and after_url and after_url != before_url:
            try:
                on_orient(after_url)
            except Exception:  # noqa: BLE001 - the warm-up hook is best-effort.
                pass
        anchor = make_anchor(before_url, after_url)
        advanced = evaluate_success_check(
            check_name, merged, before_map, fresh, anchor  # type: ignore[arg-type]
        )

        # Forward ONLY the kernel's genuinely-NEW reducer-channel entries (§18.7),
        # content-keyed so a consent re-execution can't double-count.
        prior_trace_keys = {_trace_key(e) for e in state["trace"]}
        prior_consent_keys = {_consent_key(c) for c in state["consent_log"]}
        kernel_new_trace = [
            e for e in merged["trace"] if _trace_key(e) not in prior_trace_keys
        ]
        kernel_new_consent = [
            c
            for c in merged["consent_log"]
            if _consent_key(c) not in prior_consent_keys
        ]

        update: dict = {
            "page_index": merged["page_index"],
            "grounded_facts": merged["grounded_facts"],
            "pending_proposal": merged["pending_proposal"],
            "pending_step": merged.get("pending_step"),
            # Carry the accumulated decided-step trajectory across subgoals/replans
            # so the reasoner's history is the WHOLE run, not just this drive.
            "step_history": merged.get("step_history", state.get("step_history", [])),
            "success_check": merged.get("success_check", ""),
            "paired_facts": paired,
            "_kernel_threads": merged.get("_kernel_threads", {}),
            "_before_map": before_map,
            "_anchor": anchor,
            "trace": kernel_new_trace
            + [
                _trace(
                    "EXECUTOR",
                    "exit",
                    subgoal=idx,
                    done=advanced,
                    success_check=check_name,
                    url_before=before_url or "",
                    url_after=after_url or "",
                )
            ],
        }
        if kernel_new_consent:
            update["consent_log"] = kernel_new_consent

        # The replan signal the NEXT decision sees (DecideContext.last_outcome): on a
        # failed subgoal, tell the reasoner what it just tried + why it isn't done, so
        # a retry changes strategy (e.g. act instead of re-reading) rather than
        # repeating itself. Cleared on success so a fresh subgoal starts clean.
        if advanced:
            update["last_outcome"] = ""
        else:
            pp = merged.get("pending_proposal")
            last_kind = pp.action.kind if (pp is not None and pp.action is not None) else "?"
            attempts = int(state.get("_replan_attempts", 0) or 0)
            update["last_outcome"] = (
                f"Subgoal {idx} ('{subgoal.description}') is NOT done yet — the last "
                f"action was a '{last_kind}' and the check '{check_name}' did not pass "
                f"(attempt {attempts + 1}). Choose a DIFFERENT action this time; if the "
                f"goal is to open/navigate, click or navigate the matching control "
                f"instead of reading it."
            )

        # End-of-flow "remember?" bookkeeping — ONLY when the offer is active, so a
        # memory-off run (every frozen test) keeps the executor byte-for-byte. Harvest
        # the field THIS drive filled: node_id ← the before-map the fill index pointed
        # into, value ← the acted Action; accumulate across subgoals/replans for the
        # end-of-flow nominator. Gated on the ACT once-flag so an unacted/rejected
        # fill is never captured.
        if remember_nominate is not None:
            new_filled = dict(state.get("_filled") or {})
            pp = merged.get("pending_proposal")
            if (
                pp is not None
                and pp.action is not None
                and pp.action.kind == "fill"
                and pp.action.index is not None
                and pp.action.value
                and any(
                    e.node == "ACT" and e.data.get("acted_proposal_id") == pp.id
                    for e in merged["trace"]
                )
            ):
                node = before_map.nodes.get(pp.action.index)
                if node is not None:
                    new_filled[node.node_id] = pp.action.value
            update["_filled"] = new_filled

        if advanced:
            update["stage_idx"] = idx + 1
            update["_replan_attempts"] = 0
            update["_rescue_done_for"] = None
            # Next subgoal; on the LAST subgoal, route into the end-of-flow consent
            # chain (save-workflow? → remember? → END), each self-gating (no memory
            # without a yes). Straight to END when nothing is active.
            if idx + 1 < len(subgoals):
                goto = _EXECUTOR
            else:
                goto = _end_of_flow_entry()
            return Command(update=update, goto=goto)

        # Done-check failed → the replanner (bounded retry).
        return Command(update=update, goto=_REPLANNER)

    # ---- RESCUE sub-flow (KEPT verbatim) ---------------------------------
    async def rescue(state: _StageState) -> Command:
        """The choked-widget cross-cut (foundation §4 — the most-validated trigger).
        Re-perceive (a real impl relabels via the vision fallback) and return to the
        executor. ``_rescue_done_for`` stops an immediate re-trigger on the same
        tree."""
        fresh = await actuator.perceive()
        still_choked = detect_rescue(fresh)
        return Command(
            update={
                "page_index": fresh,
                "_rescue_return": None,
                "trace": [
                    _trace(
                        "RESCUE",
                        "exit",
                        returned_to=_EXECUTOR,
                        resolved=not still_choked,
                        remaining_choked=[n.index for n in still_choked],
                    )
                ],
            },
            goto=_EXECUTOR,
        )

    # ---- replanner (KEPT) ------------------------------------------------
    async def replanner(state: _StageState) -> Command:
        """Bounded retry of the CURRENT subgoal: re-perceive and re-run it up to
        ``max_replans`` times, then give up to END so a wedged page can't loop."""
        attempts = int(state.get("_replan_attempts", 0) or 0) + 1
        idx = int(state.get("stage_idx", 0) or 0)
        if attempts > max_replans:
            return Command(
                update={
                    "trace": [
                        _trace(
                            "REPLANNER",
                            "exit",
                            gave_up=True,
                            subgoal=idx,
                            attempts=attempts,
                        )
                    ]
                },
                goto=END,
            )
        fresh = await actuator.perceive()
        return Command(
            update={
                "page_index": fresh,
                "_replan_attempts": attempts,
                "trace": [
                    _trace("REPLANNER", "exit", retrying=idx, attempts=attempts)
                ],
            },
            goto=_EXECUTOR,
        )

    # ---- end-of-flow routing (save-workflow? → remember? → END) ----------
    _save_active = offer_workflow_save and memory is not None

    def _end_of_flow_entry() -> str:
        """The first active end-of-flow node on a completed run, else END."""
        if _save_active:
            return _SAVE_WORKFLOW
        if remember_nominate is not None:
            return _REMEMBER
        return END

    def _after_save_workflow() -> str:
        """Where the save-workflow node hands off: the preference offer if active,
        else END."""
        return _REMEMBER if remember_nominate is not None else END

    # ---- save_workflow (end-of-flow "save this workflow?" offer) ----------
    async def save_workflow(state: _StageState) -> Command:
        """The consent-gated, end-of-flow EPISODE capture (the completed-workflow
        record — knowledge-layer #4b). Reached ONLY on a COMPLETED flow when active.

        Projects the finished run into a ``WorkflowEpisode`` (plan SHAPE + consent +
        filled-field count, NEVER a grounded value), and offers to remember it ONLY
        when it ``is_workflow()`` — a real multi-step / form / transactional run, not
        a trivial one-step read (which re-grounds every time and is nothing to
        repeat). The offer is ONE ``ConsentRequest`` via ``interrupt()`` — the voice
        plane speaks it and resumes with the spoken yes/no through the SAME consent
        loop as every other gate — and the episode is written through the ``Memory``
        port ONLY on an explicit "yes" (no memory without a yes). Best-effort: a
        memory miss is swallowed, never failing the finished run."""
        host = _host_of(await _current_url(actuator))
        episode = _episode_from_state(dict(state), host)
        if not episode.is_workflow():
            # A trivial read — nothing worth remembering; hand off silently.
            return Command(
                update={
                    "trace": [_trace("SAVE_WORKFLOW", "exit", offered=False)]
                },
                goto=_after_save_workflow(),
            )
        decision_payload = interrupt(
            ConsentRequest(
                proposal_id="save_workflow",
                utterance=_save_workflow_offer_utterance(episode.goal, host),
                irreversible=False,
            ).model_dump()
        )
        decision = ConsentDecision.model_validate(decision_payload)
        saved = False
        if decision.decision == "approve" and memory is not None:
            try:
                await memory.write_episode(user_id, episode)
                saved = True
            except Exception:  # noqa: BLE001 — a memory miss must never break the run.
                saved = False
        return Command(
            update={
                "trace": [
                    _trace(
                        "SAVE_WORKFLOW",
                        "exit",
                        offered=True,
                        kept=decision.decision == "approve",
                        saved=saved,
                        n_subgoals=len(episode.subgoals),
                        n_filled=episode.n_filled,
                    )
                ]
            },
            goto=_after_save_workflow(),
        )

    # ---- remember (end-of-flow "remember?" offer — no memory without a yes) ----
    async def remember(state: _StageState) -> Command:
        """The consent-gated, end-of-flow preference capture (the third invariant
        clause). Reached ONLY on a COMPLETED flow when the offer is active. The
        injected ``remember_nominate`` turns the filled fields into reusable
        ``(key, value)`` candidates with SECRETS ALREADY SUPPRESSED (a password / OTP
        / CVV is never even offered); the offer is surfaced as ONE batched
        ``ConsentRequest`` via ``interrupt()`` — the voice plane speaks it and resumes
        with the spoken yes/no through the SAME consent loop as every other gate — and
        the kept candidates are written through the ``Memory`` port ONLY on an
        explicit "yes" (never on reject/silence). Best-effort: a memory miss is
        swallowed, never failing the finished run."""
        candidates = (
            remember_nominate(dict(state.get("_filled") or {}), state["page_index"])
            if remember_nominate is not None
            else []
        )
        if not candidates:
            return Command(goto=END)
        decision_payload = interrupt(
            ConsentRequest(
                proposal_id="remember",
                utterance=_remember_offer_utterance(candidates),
                irreversible=False,
            ).model_dump()
        )
        decision = ConsentDecision.model_validate(decision_payload)
        written = 0
        if decision.decision == "approve" and memory is not None:
            for key, value in candidates:
                try:
                    await memory.write_preference(user_id, key, value, origin="stated")
                    written += 1
                except Exception:  # noqa: BLE001 — a memory miss must never break the run.
                    pass
        return Command(
            update={
                "trace": [
                    _trace(
                        "REMEMBER",
                        "exit",
                        offered=len(candidates),
                        kept=decision.decision == "approve",
                        written=written,
                    )
                ]
            },
            goto=END,
        )

    # ---- assemble --------------------------------------------------------
    builder = StateGraph(_StageState)
    builder.add_node(_PLANNER, planner)
    builder.add_node(_EXECUTOR, executor)
    builder.add_node(_RESCUE, rescue)
    builder.add_node(_REPLANNER, replanner)
    builder.add_node(_SAVE_WORKFLOW, save_workflow)
    builder.add_node(_REMEMBER, remember)

    builder.add_edge(START, _PLANNER)
    # All routing out of planner / executor / rescue / replanner is via Command(goto).

    return builder.compile(checkpointer=make_stage_checkpointer())


def seed_stage_state(
    goal: str = "",
    mode: Literal["normal", "fast"] = "normal",
    page_index: Optional[SelectorMap] = None,
) -> ClarionState:
    """A minimal valid ``ClarionState`` to start the executor graph. Reuses the
    kernel's ``seed_state`` (single source of truth for the state shape) and lets
    the caller seed the initial ``page_index`` (the freshly perceived tree). The
    ``goal`` is the real (restated) user goal — never a baked task."""
    state = seed_state(goal=goal, mode=mode)
    if page_index is not None:
        state["page_index"] = page_index
    # Per-run nonce that namespaces the inner-kernel thread ids (see `_drive_kernel`):
    # a fresh goal → a fresh `_run_id` → no collision with a prior run's parked/ended
    # inner kernel (the kernel's checkpointer outlives a single goal).
    state["_run_id"] = uuid.uuid4().hex  # type: ignore[typeddict-unknown-key]
    return state


__all__ = [
    "build_stage_graph",
    "seed_stage_state",
    "make_stage_checkpointer",
]
