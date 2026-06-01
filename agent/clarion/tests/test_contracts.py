"""C1 contract smoke test (execution §15 C1 acceptance).

Proves the three acceptance criteria:
  (a) every interface + fake imports, and the fakes satisfy the ABCs;
  (b) ``ClarionState`` round-trips through a LangGraph checkpointer
      (write state, read it back via thread_id);
  (c) a no-op ``StateGraph`` that calls ``interrupt()`` pauses, and resuming with
      ``Command(resume=...)`` completes — exercised with the fakes.

Pure contract test: it stands up an InMemorySaver-backed graph, but imports zero
provider SDKs (foundation §6).
"""

from __future__ import annotations

import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.constants import START
from langgraph.graph import StateGraph
from langgraph.types import Command, interrupt

from clarion.contracts.events import (
    AdvanceTaskRequest,
    ConsentDecision,
    ConsentRequest,
    PanelState,
)
from clarion.contracts.ports import (
    Actuator,
    Ingest,
    Memory,
    Retriever,
    SpeechHandle,
    Synthesizer,
    VoiceTransport,
)
from clarion.contracts.state import (
    Action,
    AxNode,
    ClarionState,
    Consent,
    Fact,
    Observation,
    PageDiff,
    Passage,
    Profile,
    Proposal,
    SelectorMap,
    Stage,
    TraceEvent,
)
from clarion.fakes import (
    FakeActuator,
    FakeIngest,
    FakeMemory,
    FakeRetriever,
    FakeSpeechHandle,
    FakeSynthesizer,
    FakeVoiceTransport,
)


# ---------------------------------------------------------------------------
# (a) every interface + fake imports, and the fakes satisfy the ABCs
# ---------------------------------------------------------------------------


def test_fakes_satisfy_port_abcs() -> None:
    assert isinstance(FakeVoiceTransport(), VoiceTransport)
    assert isinstance(FakeRetriever(), Retriever)
    assert isinstance(FakeSynthesizer(), Synthesizer)
    assert isinstance(FakeActuator(), Actuator)
    assert isinstance(FakeIngest(), Ingest)
    assert isinstance(FakeMemory(), Memory)


def test_fake_speech_handle_satisfies_protocol() -> None:
    # SpeechHandle is a runtime_checkable structural Protocol.
    handle = FakeSpeechHandle(interrupted=False)
    assert isinstance(handle, SpeechHandle)
    assert handle.interrupted is False


def test_ports_are_abstract() -> None:
    # The ABCs must not be directly instantiable (they have abstract methods).
    for abc_cls in (VoiceTransport, Retriever, Synthesizer, Actuator, Ingest, Memory):
        with pytest.raises(TypeError):
            abc_cls()  # type: ignore[abstract]


def test_state_and_event_models_construct() -> None:
    # Every value object + event model builds with sane inputs (pydantic v2).
    node = AxNode(index=0, role="button", name="Submit", node_id="n0")
    sm = SelectorMap(nodes={0: node}, token_estimate=12)
    fact = Fact(value="$42.00", source_node_id="n0", verified=True)
    action = Action(kind="fill", index=0, value="42", irreversible=False)
    proposal = Proposal(id="p1", utterance="fill amount", action=action)
    assert sm.nodes[0].name == "Submit"
    assert fact.polarity == "present"
    assert proposal.action is not None

    # negative verification is first-class
    negative = Fact(value="late fee", source_node_id="n1", polarity="absent")
    assert negative.polarity == "absent"

    Stage(id="FILL", goal="fill the form", tools=["read", "fill"])
    Consent(proposal_id="p1", decision="approve")
    TraceEvent(node="GROUND", event="info", data={"retrieval_ms": 6})
    Observation(selector_map=sm, success=True)
    PageDiff(added=[1], removed=[], changed=[])
    Passage(text="hello", ref="r0")
    Profile(user_id="u1")

    AdvanceTaskRequest(user_intent="pay my electric bill")
    cr = ConsentRequest(proposal_id="p1", utterance="say yes to fill")
    assert cr.options == ["yes", "no", "edit"]
    ConsentDecision(decision="approve")
    PanelState(stage="FILL", step=(1, 3))


@pytest.mark.asyncio
async def test_fakes_behave_deterministically() -> None:
    # Retriever returns a grounded fact (source_node_id present).
    facts = await FakeRetriever().query("amount due")
    assert facts and facts[0].source_node_id is not None

    # Synthesizer streams audio bytes.
    chunks = [c async for c in FakeSynthesizer().synthesize("two words")]
    assert chunks == [b"two", b"words"]

    # Actuator: perceive -> fill -> diff shows a change; click submit adds a node.
    act = FakeActuator()
    before = await act.perceive()
    assert len(before.nodes) == 2
    obs = await act.act(Action(kind="fill", index=0, value="42"))
    diff = await act.diff(before, obs.selector_map)
    assert diff.changed and not diff.is_empty
    submit_idx = next(i for i, n in obs.selector_map.nodes.items() if n.role == "button")
    after_click = await act.act(Action(kind="click", index=submit_idx))
    confirm_diff = await act.diff(obs.selector_map, after_click.selector_map)
    assert confirm_diff.added  # confirmation node appeared

    # Ingest -> queryable passages; Memory write/read round-trips.
    passages = await FakeIngest().ingest("para one\n\npara two")
    assert [p.text for p in passages] == ["para one", "para two"]
    mem = FakeMemory()
    await mem.write(Fact(value="user prefers email", source_node_id="n0"))
    profile = await mem.read_profile("default")
    assert profile.facts and profile.facts[0].value == "user prefers email"


# ---------------------------------------------------------------------------
# (b) ClarionState round-trips through a LangGraph checkpointer
# ---------------------------------------------------------------------------


def _seed_state() -> ClarionState:
    node = AxNode(index=0, role="textbox", name="Amount", node_id="node-amount")
    return ClarionState(
        goal="pay my electric bill",
        mode="normal",
        plan=[Stage(id="FILL", goal="fill the form", tools=["read", "fill"])],
        stage_idx=0,
        step=(1, 3),
        page_index=SelectorMap(nodes={0: node}, token_estimate=14),
        grounded_facts=[Fact(value="$42.00", source_node_id="node-amount", verified=True)],
        pending_proposal=Proposal(
            id="p1",
            utterance="fill amount with 42",
            action=Action(kind="fill", index=0, value="42"),
        ),
        consent_log=[Consent(proposal_id="p0", decision="approve")],
        trace=[TraceEvent(node="GROUND", event="exit", data={"retrieval_ms": 6})],
    )


def test_clarion_state_round_trips_through_checkpointer() -> None:
    """Write the full goal-state into a checkpointer-backed graph, then read it
    back by thread_id via get_state — proving pydantic models inside the TypedDict
    survive JsonPlusSerializer (execution §2.1 durability claim)."""

    def write_node(state: ClarionState) -> ClarionState:
        return state  # identity: just persist what we were handed

    builder = StateGraph(ClarionState)
    builder.add_node("write", write_node)
    builder.add_edge(START, "write")
    graph = builder.compile(checkpointer=InMemorySaver())

    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    seed = _seed_state()
    graph.invoke(seed, config)

    snapshot = graph.get_state(config)
    values = snapshot.values

    # Scalars survive verbatim.
    assert values["goal"] == "pay my electric bill"
    assert values["mode"] == "normal"
    assert values["stage_idx"] == 0

    # Pydantic models round-trip as pydantic models (not bare dicts).
    page_index = values["page_index"]
    assert isinstance(page_index, SelectorMap)
    assert page_index.nodes[0].name == "Amount"

    assert isinstance(values["plan"][0], Stage)
    assert values["plan"][0].id == "FILL"

    facts = values["grounded_facts"]
    assert isinstance(facts[0], Fact)
    assert facts[0].source_node_id == "node-amount" and facts[0].verified is True

    proposal = values["pending_proposal"]
    assert isinstance(proposal, Proposal)
    assert proposal.action is not None and proposal.action.value == "42"

    assert isinstance(values["consent_log"][0], Consent)
    assert isinstance(values["trace"][0], TraceEvent)
    assert values["trace"][0].data["retrieval_ms"] == 6

    # step is a (k, n) pair; JsonPlusSerializer round-trips tuples as tuples here,
    # but consumers should treat it structurally. Compare element-wise.
    assert tuple(values["step"]) == (1, 3)


# ---------------------------------------------------------------------------
# (c) interrupt() pauses; Command(resume=...) completes — using the fakes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interrupt_pauses_and_resume_completes_with_fakes() -> None:
    """A minimal no-op graph that mirrors PROPOSE -> ⟨CONSENT⟩ -> ACT.

    The CONSENT node raises an interrupt carrying a ConsentRequest (the real
    payload shape). Resuming with Command(resume=ConsentDecision(...)) completes
    the run; the approved action is then executed through the FakeActuator and
    written to the consent_log — proving the fakes drive the seam.
    """
    voice = FakeVoiceTransport()
    actuator = FakeActuator()

    def propose_node(state: ClarionState) -> dict:
        proposal = Proposal(
            id="p1",
            utterance="Fill the amount field with 42. Say yes to continue.",
            action=Action(kind="fill", index=0, value="42"),
        )
        return {"pending_proposal": proposal}

    def consent_node(state: ClarionState) -> dict:
        proposal = state["pending_proposal"]
        assert proposal is not None
        # The fake voice plane "speaks" the readback (records it).
        # interrupt() surfaces the real ConsentRequest payload to the client.
        decision_payload = interrupt(
            ConsentRequest(
                proposal_id=proposal.id,
                utterance=proposal.utterance,
                irreversible=proposal.irreversible,
            ).model_dump()
        )
        decision = ConsentDecision.model_validate(decision_payload)
        return {
            "consent_log": [
                Consent(proposal_id=proposal.id, decision=decision.decision)
            ]
        }

    async def act_node(state: ClarionState) -> dict:
        # Idempotency guard (execution §2.3): only act once per approved proposal.
        proposal = state["pending_proposal"]
        assert proposal is not None and proposal.action is not None
        approved = any(
            c.proposal_id == proposal.id and c.decision == "approve"
            for c in state["consent_log"]
        )
        if approved:
            obs = await actuator.act(proposal.action)
            return {"page_index": obs.selector_map}
        return {}

    builder = StateGraph(ClarionState)
    builder.add_node("propose", propose_node)
    builder.add_node("consent", consent_node)
    builder.add_node("act", act_node)
    builder.add_edge(START, "propose")
    builder.add_edge("propose", "consent")
    builder.add_edge("consent", "act")
    graph = builder.compile(checkpointer=InMemorySaver())

    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    seed = _seed_state()
    # Clear the seeded proposal/consent so the run produces them fresh.
    seed["pending_proposal"] = None
    seed["consent_log"] = []

    # First pass: the graph PAUSES at the consent interrupt.
    result = await graph.ainvoke(seed, config)
    assert "__interrupt__" in result
    (interrupt_obj,) = result["__interrupt__"]
    surfaced = ConsentRequest.model_validate(interrupt_obj.value)
    assert surfaced.proposal_id == "p1"
    assert "Say yes" in surfaced.utterance

    # The agent is still parked at consent — ACT has not run yet.
    parked = graph.get_state(config)
    assert parked.next == ("consent",)
    assert actuator.acted == []  # no side-effect before the yes

    # Voice plane speaks the readback (deterministic fake).
    await voice.say(surfaced.utterance, interruptible=True)
    assert voice.spoken[-1][0] == surfaced.utterance

    # Resume with the approval decision.
    decision = ConsentDecision(decision="approve")
    final = await graph.ainvoke(Command(resume=decision.model_dump()), config)

    # The run COMPLETED (no more interrupts, no pending next node).
    assert "__interrupt__" not in final
    done = graph.get_state(config)
    assert done.next == ()

    # The approved action was executed exactly once through the fake actuator.
    assert len(actuator.acted) == 1
    assert actuator.acted[0].kind == "fill" and actuator.acted[0].value == "42"

    # And the consent decision was logged.
    assert final["consent_log"][-1].decision == "approve"


@pytest.mark.asyncio
async def test_resume_reject_does_not_act() -> None:
    """The agentic clause: a 'reject' decision completes the run WITHOUT executing
    the side-effect (foundation §1 — no action without a yes)."""
    actuator = FakeActuator()

    def propose_node(state: ClarionState) -> dict:
        return {
            "pending_proposal": Proposal(
                id="p9",
                utterance="Submit the payment. Say yes to continue.",
                action=Action(kind="click", index=1, irreversible=True),
                irreversible=True,
            )
        }

    def consent_node(state: ClarionState) -> dict:
        proposal = state["pending_proposal"]
        assert proposal is not None
        payload = interrupt(
            ConsentRequest(
                proposal_id=proposal.id,
                utterance=proposal.utterance,
                irreversible=True,
            ).model_dump()
        )
        decision = ConsentDecision.model_validate(payload)
        return {"consent_log": [Consent(proposal_id=proposal.id, decision=decision.decision)]}

    async def act_node(state: ClarionState) -> dict:
        proposal = state["pending_proposal"]
        assert proposal is not None and proposal.action is not None
        approved = any(
            c.proposal_id == proposal.id and c.decision == "approve"
            for c in state["consent_log"]
        )
        if approved:
            await actuator.act(proposal.action)
        return {}

    builder = StateGraph(ClarionState)
    builder.add_node("propose", propose_node)
    builder.add_node("consent", consent_node)
    builder.add_node("act", act_node)
    builder.add_edge(START, "propose")
    builder.add_edge("propose", "consent")
    builder.add_edge("consent", "act")
    graph = builder.compile(checkpointer=InMemorySaver())

    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    seed = _seed_state()
    seed["pending_proposal"] = None
    seed["consent_log"] = []

    result = await graph.ainvoke(seed, config)
    assert "__interrupt__" in result

    final = await graph.ainvoke(Command(resume=ConsentDecision(decision="reject").model_dump()), config)
    assert "__interrupt__" not in final
    # No yes => no irreversible side-effect.
    assert actuator.acted == []
    assert final["consent_log"][-1].decision == "reject"
