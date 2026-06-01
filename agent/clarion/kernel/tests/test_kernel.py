"""K1 — kernel acceptance tests (execution §15 K1 / §2).

All six required conditions, each isolated:
  (1) VERIFY refuses an ungrounded fact (source_node_id=None) — not marked verified.
  (2) Normal mode pauses at ⟨CONSENT⟩ for a consequential step.
  (3) Fast mode auto-proceeds on a reversible proposal but still interrupts an
      irreversible one.
  (4) resume(approve) twice → ACT called exactly once (idempotency, §2.3).
  (5) Trace events are emitted per node.
  (6) Checkpointed ClarionState round-trips with NO deserialization warnings.

Pure: uses the FakeRetriever/FakeActuator from clarion.fakes; imports zero
provider SDKs (foundation §6).
"""

from __future__ import annotations

import uuid
import warnings

import pytest
from langgraph.types import Command

from clarion.contracts.events import ConsentDecision
from clarion.contracts.ports import Actuator, Retriever
from clarion.contracts.state import (
    Action,
    AxNode,
    Fact,
    Observation,
    PageDiff,
    Proposal,
    SelectorMap,
)
from clarion.fakes import FakeActuator
from clarion.kernel.graph import build_kernel, seed_state
from clarion.kernel.policy import (
    PolicyViolation,
    assert_consented,
    assert_grounded,
    is_grounded,
    speakable,
)


def _cfg() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


# A retriever that returns exactly the facts it was handed (ungrounded ones too),
# so we can drive VERIFY's refusal path deterministically.
class _ScriptedRetriever(Retriever):
    def __init__(self, facts: list[Fact]) -> None:
        self._facts = facts

    async def query(self, q: str, *, k: int = 5) -> list[Fact]:  # noqa: ARG002
        return list(self._facts)[:k]


# An actuator that counts real acts, so idempotency is provable by a call count.
class _CountingActuator(Actuator):
    """Counts real acts so idempotency is provable by call count.

    ``page`` controls what PROPOSE sees:
      - "fill"   → a textbox present → reversible fill proposal
      - "submit" → only a Pay button → irreversible click proposal
    """

    def __init__(self, page: str = "fill") -> None:
        self.act_calls: list[Action] = []
        self._page = page

    def _map(self) -> SelectorMap:
        if self._page == "submit":
            return SelectorMap(
                nodes={
                    0: AxNode(index=0, role="button", name="Pay", node_id="n-pay"),
                },
                token_estimate=10,
            )
        return SelectorMap(
            nodes={
                0: AxNode(index=0, role="textbox", name="Amount", node_id="n-amount"),
                1: AxNode(index=1, role="button", name="Pay", node_id="n-pay"),
            },
            token_estimate=20,
        )

    async def perceive(self) -> SelectorMap:
        return self._map()

    async def act(self, action: Action) -> Observation:
        self.act_calls.append(action)
        return Observation(selector_map=self._map(), success=True)

    async def diff(self, before: SelectorMap, after: SelectorMap) -> PageDiff:
        return PageDiff()


def _grounded_fact(value: str = "$42.00") -> Fact:
    return Fact(value=value, source_node_id="n-amount", retrieved_at=0.0)


def _ungrounded_fact(value: str = "made up balance") -> Fact:
    return Fact(value=value, source_node_id=None, retrieved_at=0.0)


# ---------------------------------------------------------------------------
# (1) VERIFY refuses an ungrounded fact
# ---------------------------------------------------------------------------


def test_policy_refuses_ungrounded_fact_unit() -> None:
    """The epistemic clause in isolation: assert_grounded never marks an
    ungrounded fact verified, even if it arrived claiming verified=True."""
    facts = [
        _grounded_fact(),
        Fact(value="hallucinated", source_node_id=None, verified=True),  # lies
    ]
    checked = assert_grounded(facts)
    grounded, ungrounded = checked[0], checked[1]
    assert is_grounded(grounded) and grounded.verified is True
    assert not is_grounded(ungrounded)
    assert ungrounded.verified is False  # refused — cannot be promoted
    # And it is not speakable.
    assert ungrounded not in speakable(checked)
    assert grounded in speakable(checked)


@pytest.mark.asyncio
async def test_verify_node_refuses_ungrounded_fact_in_graph() -> None:
    """In the live graph: feed GROUND an ungrounded fact; after VERIFY it is in
    grounded_facts but NOT marked verified (so PROPOSE/say can never speak it)."""
    retriever = _ScriptedRetriever([_ungrounded_fact()])
    actuator = FakeActuator()
    graph = build_kernel(retriever, actuator, mode="fast")

    seed = seed_state(goal="what is my balance", mode="fast")
    seed["page_index"] = await actuator.perceive()
    final = await graph.ainvoke(seed, _cfg())

    facts = final["grounded_facts"]
    assert len(facts) == 1
    assert facts[0].source_node_id is None
    assert facts[0].verified is False  # VERIFY refused to verify it
    assert speakable(facts) == []  # nothing speakable
    # The VERIFY trace records one refusal, zero verified.
    verify_ev = next(e for e in final["trace"] if e.node == "VERIFY" and e.event == "exit")
    assert verify_ev.data["verified"] == 0
    assert verify_ev.data["refused"] == 1


# ---------------------------------------------------------------------------
# (2) Normal mode pauses at CONSENT for a consequential step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_mode_pauses_at_consent() -> None:
    retriever = _ScriptedRetriever([_grounded_fact()])
    actuator = _CountingActuator()
    graph = build_kernel(retriever, actuator, mode="normal")

    seed = seed_state(goal="pay my electric bill", mode="normal")
    seed["page_index"] = await actuator.perceive()
    config = _cfg()

    result = await graph.ainvoke(seed, config)
    # PAUSED at the consent interrupt — a consequential (fill) step in Normal mode.
    assert "__interrupt__" in result
    (interrupt_obj,) = result["__interrupt__"]
    assert interrupt_obj.value["utterance"]  # the readback to speak
    assert interrupt_obj.value["irreversible"] is False

    parked = graph.get_state(config)
    assert parked.next == ("consent",)
    assert actuator.act_calls == []  # no side-effect before the yes


# ---------------------------------------------------------------------------
# (3) Fast mode: auto-proceed on reversible, interrupt on irreversible
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_mode_auto_proceeds_on_reversible() -> None:
    """A reversible (fill) proposal in Fast mode runs end-to-end with NO interrupt
    and the act fires once, without any consent in the log."""
    retriever = _ScriptedRetriever([_grounded_fact()])
    actuator = _CountingActuator()
    graph = build_kernel(retriever, actuator, mode="fast")

    seed = seed_state(goal="pay my electric bill", mode="fast")
    seed["page_index"] = await actuator.perceive()
    config = _cfg()

    final = await graph.ainvoke(seed, config)
    assert "__interrupt__" not in final  # auto-proceeded
    assert graph.get_state(config).next == ()  # completed
    assert len(actuator.act_calls) == 1  # the reversible act fired
    assert final["consent_log"] == []  # no gate was armed


@pytest.mark.asyncio
async def test_fast_mode_still_interrupts_irreversible() -> None:
    """End-to-end: in Fast mode, a page with only a Pay button makes PROPOSE form
    an IRREVERSIBLE click — which STILL hits the consent gate (foundation §5
    hard-stop). The act must NOT fire until a yes arrives."""
    retriever = _ScriptedRetriever([_grounded_fact()])
    actuator = _CountingActuator(page="submit")  # only an irreversible Pay button
    graph = build_kernel(retriever, actuator, mode="fast")

    seed = seed_state(goal="pay my electric bill", mode="fast")
    seed["page_index"] = await actuator.perceive()
    config = _cfg()

    result = await graph.ainvoke(seed, config)
    # Fast mode did NOT auto-proceed: the irreversible proposal armed the gate.
    assert "__interrupt__" in result
    (interrupt_obj,) = result["__interrupt__"]
    assert interrupt_obj.value["irreversible"] is True
    assert "cannot be undone" in interrupt_obj.value["utterance"]
    assert graph.get_state(config).next == ("consent",)
    assert actuator.act_calls == []  # hard-stopped before any side-effect

    # And once approved it acts exactly once.
    final = await graph.ainvoke(
        Command(resume=ConsentDecision(decision="approve").model_dump()), config
    )
    assert "__interrupt__" not in final
    assert len(actuator.act_calls) == 1


# ---------------------------------------------------------------------------
# (4) resume(approve) twice → ACT called exactly once (idempotency)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_approve_twice_acts_once() -> None:
    retriever = _ScriptedRetriever([_grounded_fact()])
    actuator = _CountingActuator()
    graph = build_kernel(retriever, actuator, mode="normal")

    seed = seed_state(goal="pay my electric bill", mode="normal")
    seed["page_index"] = await actuator.perceive()
    config = _cfg()

    # Run to the consent interrupt.
    paused = await graph.ainvoke(seed, config)
    assert "__interrupt__" in paused
    assert actuator.act_calls == []

    approve = ConsentDecision(decision="approve").model_dump()

    # First resume(approve): completes, acts exactly once.
    final1 = await graph.ainvoke(Command(resume=approve), config)
    assert "__interrupt__" not in final1
    assert len(actuator.act_calls) == 1

    # Second resume(approve) delivered to the SAME thread (the faithful §2.3
    # scenario — a duplicate "yes" re-delivered to the interrupted thread). The
    # thread is already at END after resume #1, so this is a no-op: still exactly
    # one real act, no double-fill. This is the exact pattern the S1 seam-spike
    # gate validated against the live LiveKit↔LangGraph↔CDP seam.
    final2 = await graph.ainvoke(Command(resume=approve), config)
    assert "__interrupt__" not in final2
    assert len(actuator.act_calls) == 1, (
        f"double-act on same-thread re-resume: {len(actuator.act_calls)} calls"
    )

    # And the once-flag survives a deliberate fork that FORCES the ACT node to
    # re-execute. We fork from the parked-at-consent checkpoint and inject the prior
    # ACT once-flag marker — modelling the marker the reducer-accumulated trace
    # carries forward across a real interrupt re-execution. ``trace`` is now an
    # ``operator.add`` reducer channel, so update_state APPENDS just the marker (we
    # pass only the new entry, never the prior trace — that would double-count).
    # On resume, CONSENT re-runs, ACT re-enters, sees the marker, and skips the
    # re-fill (§2.3 "node re-executes from the top" exercised head-on).
    history = list(graph.get_state_history(config))
    parked = [s for s in history if s.next == ("consent",)][-1]
    marker = next(
        e for e in final1["trace"]
        if e.node == "ACT" and e.data.get("acted_proposal_id")
    )
    forked_cfg = graph.update_state(parked.config, {"trace": [marker]})
    forked = await graph.ainvoke(Command(resume=approve), forked_cfg)
    assert "__interrupt__" not in forked
    # The re-executed ACT did NOT fill again — still exactly one real act total.
    assert len(actuator.act_calls) == 1, (
        f"double-act on fork-replay: {len(actuator.act_calls)} calls"
    )
    skipped = [
        e for e in forked["trace"]
        if e.node == "ACT" and e.data.get("skipped") == "already-acted"
    ]
    assert skipped, "expected an 'already-acted' ACT skip marker on the re-run"


# ---------------------------------------------------------------------------
# (5) Trace events emitted per node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trace_events_emitted_per_node() -> None:
    retriever = _ScriptedRetriever([_grounded_fact()])
    actuator = _CountingActuator()
    graph = build_kernel(retriever, actuator, mode="normal")

    seed = seed_state(goal="pay my electric bill", mode="normal")
    seed["page_index"] = await actuator.perceive()
    config = _cfg()

    paused = await graph.ainvoke(seed, config)
    assert "__interrupt__" in paused
    final = await graph.ainvoke(
        Command(resume=ConsentDecision(decision="approve").model_dump()), config
    )

    nodes_traced = {e.node for e in final["trace"]}
    # Every kernel verb emitted at least one trace event.
    for node in ("GROUND", "VERIFY", "PROPOSE", "CONSENT", "ACT", "CONFIRM"):
        assert node in nodes_traced, f"no trace event from {node}"
    # GROUND carries the latency-meter datum (§8).
    ground_ev = next(e for e in final["trace"] if e.node == "GROUND" and e.event == "exit")
    assert "retrieval_ms" in ground_ev.data


# ---------------------------------------------------------------------------
# (6) Checkpointed ClarionState round-trips with NO deserialization warnings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkpoint_roundtrip_no_warnings() -> None:
    """Run the full kernel and assert the checkpointed ClarionState round-trips
    with NO serde future-removal message (execution §18.6).

    langgraph 1.2.2 emits "Deserializing unregistered type ... blocked in a future
    version" on stderr when a pydantic model is checkpointed without being in
    ``allowed_msgpack_modules``. The kernel's checkpointer allowlists every
    contract model, so a clean round-trip emits ZERO such messages. We capture
    stderr around the full run + state read-back to prove it (and also guard with
    warnings-as-errors for belt-and-suspenders)."""
    import contextlib
    import io

    retriever = _ScriptedRetriever([_grounded_fact()])
    actuator = _CountingActuator()
    graph = build_kernel(retriever, actuator, mode="normal")

    seed = seed_state(goal="pay my electric bill", mode="normal")
    seed["page_index"] = await actuator.perceive()
    config = _cfg()

    stderr_buf = io.StringIO()
    with warnings.catch_warnings(), contextlib.redirect_stderr(stderr_buf):
        warnings.simplefilter("error", DeprecationWarning)
        first = await graph.ainvoke(seed, config)
        assert "__interrupt__" in first
        await graph.ainvoke(
            Command(resume=ConsentDecision(decision="approve").model_dump()), config
        )
        # Reading state back is what forces deserialization of the channels.
        snapshot = graph.get_state(config)
        values = snapshot.values

    captured = stderr_buf.getvalue()
    assert "Deserializing unregistered type" not in captured, (
        f"serde future-removal message leaked (missing allowlist entry?):\n{captured}"
    )
    assert "allowed_msgpack_modules" not in captured

    # State read back is fully typed (models, not dicts) — durability claim holds.
    assert isinstance(values["page_index"], SelectorMap)
    assert all(isinstance(f, Fact) for f in values["grounded_facts"])
    assert all(hasattr(c, "decision") for c in values["consent_log"])
    assert values["consent_log"][-1].decision == "approve"
    # step round-trips (as list under JsonPlus); compare structurally.
    assert tuple(values["step"]) == (0, 1)


# ---------------------------------------------------------------------------
# Bonus: the agentic clause hard-stop is real (policy raises on unconsented act)
# ---------------------------------------------------------------------------


def test_policy_hard_stops_unconsented_irreversible_act() -> None:
    irreversible = Proposal(
        id="pay-x",
        utterance="pay",
        action=Action(kind="click", index=1, irreversible=True),
        irreversible=True,
    )
    with pytest.raises(PolicyViolation):
        assert_consented(irreversible, consent_log=[])
    # A reversible action is always permitted.
    reversible = Proposal(
        id="fill-x",
        utterance="fill",
        action=Action(kind="fill", index=0, irreversible=False),
        irreversible=False,
    )
    assert assert_consented(reversible, consent_log=[]) is True
