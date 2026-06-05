"""K1 — kernel acceptance tests, Reasoner-wired (Wave-C de-hardcoding).

The kernel is now the de-hardcoded spine: PROPOSE asks the injected ``Reasoner``
for the next grounded step over the top-K hint slice, the IrreversibilityGate
classifies it, and the mode gate routes consent. These tests drive it with a
deterministic ``FakeReasoner`` (network-free) and assert the SAME invariants:

  (1) VERIFY refuses an ungrounded fact (source_node_id=None) — not marked verified.
  (2) Normal mode pauses at ⟨CONSENT⟩ for a consequential (gated) step.
  (3) Fast mode auto-proceeds on a reversible step but still interrupts an
      irreversible / unknown one (the dual-signal gate, killer-closer #2).
  (4) resume(approve) twice → ACT called exactly once (idempotency, §2.3).
  (5) Trace events emitted per node (incl. the new GATE node + decide_ms).
  (6) Checkpointed ClarionState round-trips with NO deserialization warnings.

Pure: FakeReasoner/FakeActuator from clarion.fakes; zero provider SDKs.
"""

from __future__ import annotations

import uuid
import warnings

import pytest
from langgraph.types import Command

from clarion.contracts.events import ConsentDecision
from clarion.contracts.ports import Actuator, Reasoner, Retriever
from clarion.contracts.state import (
    Action,
    AxNode,
    Fact,
    Observation,
    PageDiff,
    Proposal,
    SelectorMap,
    StepProposal,
)
from clarion.fakes import FakeActuator, FakeReasoner
from clarion.kernel.graph import build_kernel, seed_state
from clarion.kernel.policy import (
    PolicyViolation,
    assert_consented,
    assert_grounded,
    is_grounded,
    is_member,
    is_speakable_value,
    speakable,
)


def _cfg() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


class _ScriptedRetriever(Retriever):
    """Returns exactly the facts it was handed (ungrounded ones too), so VERIFY's
    refusal path is drivable deterministically."""

    def __init__(self, facts: list[Fact]) -> None:
        self._facts = facts

    async def query(self, q: str, *, k: int = 5) -> list[Fact]:  # noqa: ARG002
        return list(self._facts)[:k]


class _CountingActuator(Actuator):
    """Counts real acts so idempotency is provable by call count.

    ``page`` controls what the live map offers:
      - "fill"   → a textbox + a button
      - "submit" → only a button (the Reasoner is scripted to click it)
    """

    def __init__(self, page: str = "fill") -> None:
        self.act_calls: list[Action] = []
        self._page = page

    def _map(self) -> SelectorMap:
        if self._page == "submit":
            return SelectorMap(
                nodes={0: AxNode(index=0, role="button", name="Continue", node_id="n-c")},
                token_estimate=10,
            )
        return SelectorMap(
            nodes={
                0: AxNode(index=0, role="textbox", name="Amount", node_id="n-amount"),
                1: AxNode(index=1, role="button", name="Continue", node_id="n-c"),
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
    return Fact(value=value, source_node_id="n-amount", verified=True, retrieved_at=0.0)


def _ungrounded_fact(value: str = "made up balance") -> Fact:
    return Fact(value=value, source_node_id=None, retrieved_at=0.0)


def _fill_reasoner(facts: list[Fact]) -> FakeReasoner:
    """A reasoner scripted to fill index 0 with the first grounded fact —
    reversible. (The Fact.id is resolved at decide-time from the live facts.)"""
    fid = facts[0].id if facts else None
    return FakeReasoner(
        steps=[
            StepProposal(
                scratch_reasoning="fill the amount",
                action_kind="fill",
                target_index=0,
                value_ref=fid,
                irreversibility="reversible",
                success_check="field_nonempty",
                say=facts[0].value if facts else "",
            )
        ]
    )


def _click_reasoner(*, irreversibility: str = "irreversible") -> FakeReasoner:
    """A reasoner scripted to click index 0 — judged irreversible/unknown so the
    dual-signal gate routes it through CONSENT even in Fast mode."""
    return FakeReasoner(
        steps=[
            StepProposal(
                scratch_reasoning="press continue",
                action_kind="click",
                target_index=0,
                value_ref=None,
                irreversibility=irreversibility,  # type: ignore[arg-type]
                success_check="node_added",
                say="",
            )
        ]
    )


# ---------------------------------------------------------------------------
# (1) VERIFY refuses an ungrounded fact
# ---------------------------------------------------------------------------


def test_policy_refuses_ungrounded_fact_unit() -> None:
    facts = [
        _grounded_fact(),
        Fact(value="hallucinated", source_node_id=None, verified=True),  # lies
    ]
    checked = assert_grounded(facts)
    grounded, ungrounded = checked[0], checked[1]
    assert is_grounded(grounded) and grounded.verified is True
    assert not is_grounded(ungrounded)
    assert ungrounded.verified is False  # refused
    assert ungrounded not in speakable(checked)
    assert grounded in speakable(checked)


@pytest.mark.asyncio
async def test_verify_node_refuses_ungrounded_fact_in_graph() -> None:
    retriever = _ScriptedRetriever([_ungrounded_fact()])
    actuator = FakeActuator()
    graph = build_kernel(FakeReasoner(), retriever, actuator, mode="fast")

    seed = seed_state(goal="what is my balance", mode="fast")
    seed["page_index"] = await actuator.perceive()
    final = await graph.ainvoke(seed, _cfg())

    facts = final["grounded_facts"]
    assert len(facts) == 1
    assert facts[0].source_node_id is None
    assert facts[0].verified is False  # VERIFY refused
    assert speakable(facts) == []
    verify_ev = next(e for e in final["trace"] if e.node == "VERIFY" and e.event == "exit")
    assert verify_ev.data["verified"] == 0
    assert verify_ev.data["refused"] == 1


# ---------------------------------------------------------------------------
# (1b) the membership + pairing fence (the upgraded epistemic clause)
# ---------------------------------------------------------------------------


def test_membership_fence_rejects_non_member_value() -> None:
    """Fence #2: a value byte-identical to a live grounded Fact is a member and
    speakable; a paraphrase / fabrication is NOT a member and never speakable."""
    facts = [_grounded_fact("Amount due: $84.32")]
    assert is_member("Amount due: $84.32", facts) is True
    assert is_speakable_value("Amount due: $84.32", facts) is True
    # A restatement is not byte-identical → not a member → not speakable.
    assert is_member("the amount is $84.32", facts) is False
    assert is_speakable_value("the amount is $84.32", facts) is False
    # An ungrounded member (verified=False) is a member but NOT speakable.
    ungrounded = [Fact(value="$1.00", source_node_id=None)]
    assert is_member("$1.00", ungrounded) is True
    assert is_speakable_value("$1.00", ungrounded) is False


def test_pairing_fence_needs_a_single_backing_pair() -> None:
    """Fence #3: an 'X is Y' claim is backed ONLY by a single PairedFact joining
    both halves geometrically — two unrelated true facts do not back it."""
    from clarion.contracts.state import PairedFact
    from clarion.kernel.policy import pairing_backs

    label = Fact(value="Amount due", source_node_id="n-l")
    value = Fact(value="$84.32", source_node_id="n-v")
    pair = PairedFact(label=label, value=value, method="shared-row")
    assert pairing_backs("Amount due", "$84.32", [pair]) is True
    # A mis-pairing (the past-due row's value) is ungroundable.
    assert pairing_backs("Amount due", "$142.10", [pair]) is False
    assert pairing_backs("Amount due", "$84.32", []) is False


# ---------------------------------------------------------------------------
# (2) Normal mode pauses at CONSENT for a consequential step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_mode_pauses_at_consent() -> None:
    facts = [_grounded_fact()]
    retriever = _ScriptedRetriever(facts)
    actuator = _CountingActuator()
    graph = build_kernel(_fill_reasoner(facts), retriever, actuator, mode="normal")

    seed = seed_state(goal="enter the amount", mode="normal")
    seed["page_index"] = await actuator.perceive()
    config = _cfg()

    result = await graph.ainvoke(seed, config)
    # A consequential (fill) step in Normal mode gates.
    assert "__interrupt__" in result
    (interrupt_obj,) = result["__interrupt__"]
    assert interrupt_obj.value["utterance"]
    parked = graph.get_state(config)
    assert parked.next == ("consent",)
    assert actuator.act_calls == []  # no side-effect before the yes


# ---------------------------------------------------------------------------
# (3) Fast mode: auto-proceed on reversible, interrupt on irreversible/unknown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_mode_auto_proceeds_on_reversible() -> None:
    facts = [_grounded_fact()]
    retriever = _ScriptedRetriever(facts)
    actuator = _CountingActuator()
    graph = build_kernel(_fill_reasoner(facts), retriever, actuator, mode="fast")

    seed = seed_state(goal="enter the amount", mode="fast")
    seed["page_index"] = await actuator.perceive()
    config = _cfg()

    final = await graph.ainvoke(seed, config)
    assert "__interrupt__" not in final  # reversible auto-proceeded
    assert graph.get_state(config).next == ()
    assert len(actuator.act_calls) == 1
    assert final["consent_log"] == []
    # The fill carried the membership-fenced verbatim value.
    assert actuator.act_calls[0].kind == "fill"
    assert actuator.act_calls[0].value == "$42.00"


@pytest.mark.asyncio
async def test_fast_mode_still_interrupts_irreversible() -> None:
    """A click the Reasoner judges irreversible STILL hits the consent gate in
    Fast mode (the dual-signal gate hard-stop)."""
    facts = [_grounded_fact()]
    retriever = _ScriptedRetriever(facts)
    actuator = _CountingActuator(page="submit")
    graph = build_kernel(_click_reasoner(), retriever, actuator, mode="fast")

    seed = seed_state(goal="continue", mode="fast")
    seed["page_index"] = await actuator.perceive()
    config = _cfg()

    result = await graph.ainvoke(seed, config)
    assert "__interrupt__" in result
    (interrupt_obj,) = result["__interrupt__"]
    assert interrupt_obj.value["irreversible"] is True
    assert graph.get_state(config).next == ("consent",)
    assert actuator.act_calls == []

    final = await graph.ainvoke(
        Command(resume=ConsentDecision(decision="approve").model_dump()), config
    )
    assert "__interrupt__" not in final
    assert len(actuator.act_calls) == 1


@pytest.mark.asyncio
async def test_fast_mode_unknown_gates_like_irreversible() -> None:
    """UNKNOWN routes through CONSENT even in Fast mode (the kernel-side half of
    the UNKNOWN-gates-Fast invariant — the gate treats unknown as not-reversible)."""
    facts = [_grounded_fact()]
    retriever = _ScriptedRetriever(facts)
    actuator = _CountingActuator(page="submit")
    graph = build_kernel(
        _click_reasoner(irreversibility="unknown"), retriever, actuator, mode="fast"
    )

    seed = seed_state(goal="continue", mode="fast")
    seed["page_index"] = await actuator.perceive()
    config = _cfg()

    result = await graph.ainvoke(seed, config)
    assert "__interrupt__" in result  # unknown did NOT auto-proceed
    assert graph.get_state(config).next == ("consent",)
    assert actuator.act_calls == []


# ---------------------------------------------------------------------------
# (4) resume(approve) twice → ACT called exactly once (idempotency)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_approve_twice_acts_once() -> None:
    facts = [_grounded_fact()]
    retriever = _ScriptedRetriever(facts)
    actuator = _CountingActuator()
    graph = build_kernel(_fill_reasoner(facts), retriever, actuator, mode="normal")

    seed = seed_state(goal="enter the amount", mode="normal")
    seed["page_index"] = await actuator.perceive()
    config = _cfg()

    paused = await graph.ainvoke(seed, config)
    assert "__interrupt__" in paused
    assert actuator.act_calls == []

    approve = ConsentDecision(decision="approve").model_dump()

    final1 = await graph.ainvoke(Command(resume=approve), config)
    assert "__interrupt__" not in final1
    assert len(actuator.act_calls) == 1

    # A duplicate yes re-delivered to the same (already-ended) thread: no-op.
    final2 = await graph.ainvoke(Command(resume=approve), config)
    assert "__interrupt__" not in final2
    assert len(actuator.act_calls) == 1

    # Force the ACT node to re-execute from a forked parked-at-consent checkpoint
    # with the prior once-flag marker injected; the §2.3 guard must skip the act.
    history = list(graph.get_state_history(config))
    parked = [s for s in history if s.next == ("consent",)][-1]
    marker = next(
        e for e in final1["trace"]
        if e.node == "ACT" and e.data.get("acted_proposal_id")
    )
    forked_cfg = graph.update_state(parked.config, {"trace": [marker]})
    forked = await graph.ainvoke(Command(resume=approve), forked_cfg)
    assert "__interrupt__" not in forked
    assert len(actuator.act_calls) == 1
    skipped = [
        e for e in forked["trace"]
        if e.node == "ACT" and e.data.get("skipped") == "already-acted"
    ]
    assert skipped, "expected an 'already-acted' ACT skip marker on the re-run"


# ---------------------------------------------------------------------------
# (5) Trace events emitted per node (incl. the new GATE node)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trace_events_emitted_per_node() -> None:
    facts = [_grounded_fact()]
    retriever = _ScriptedRetriever(facts)
    actuator = _CountingActuator()
    graph = build_kernel(_fill_reasoner(facts), retriever, actuator, mode="normal")

    seed = seed_state(goal="enter the amount", mode="normal")
    seed["page_index"] = await actuator.perceive()
    config = _cfg()

    paused = await graph.ainvoke(seed, config)
    assert "__interrupt__" in paused
    final = await graph.ainvoke(
        Command(resume=ConsentDecision(decision="approve").model_dump()), config
    )

    nodes_traced = {e.node for e in final["trace"]}
    for node in ("GROUND", "VERIFY", "PROPOSE", "GATE", "CONSENT", "ACT", "CONFIRM"):
        assert node in nodes_traced, f"no trace event from {node}"
    ground_ev = next(e for e in final["trace"] if e.node == "GROUND" and e.event == "exit")
    assert "retrieval_ms" in ground_ev.data
    # PROPOSE records the decide_ms signal (the <800ms turn-budget datum).
    propose_ev = next(e for e in final["trace"] if e.node == "PROPOSE" and e.event == "exit")
    assert "decide_ms" in propose_ev.data


# ---------------------------------------------------------------------------
# (6) Checkpointed ClarionState round-trips with NO deserialization warnings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkpoint_roundtrip_no_warnings() -> None:
    import contextlib
    import io

    facts = [_grounded_fact()]
    retriever = _ScriptedRetriever(facts)
    actuator = _CountingActuator()
    graph = build_kernel(_fill_reasoner(facts), retriever, actuator, mode="normal")

    seed = seed_state(goal="enter the amount", mode="normal")
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
        snapshot = graph.get_state(config)
        values = snapshot.values

    captured = stderr_buf.getvalue()
    assert "Deserializing unregistered type" not in captured, (
        f"serde future-removal message leaked (missing allowlist entry?):\n{captured}"
    )
    assert "allowed_msgpack_modules" not in captured

    assert isinstance(values["page_index"], SelectorMap)
    assert all(isinstance(f, Fact) for f in values["grounded_facts"])
    assert all(hasattr(c, "decision") for c in values["consent_log"])
    assert values["consent_log"][-1].decision == "approve"
    assert tuple(values["step"]) == (0, 1)
    # The pending StepProposal round-tripped (the new checkpointed Reasoner I/O).
    assert isinstance(values["pending_step"], StepProposal)


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
    reversible = Proposal(
        id="fill-x",
        utterance="fill",
        action=Action(kind="fill", index=0, irreversible=False),
        irreversible=False,
    )
    assert assert_consented(reversible, consent_log=[]) is True
