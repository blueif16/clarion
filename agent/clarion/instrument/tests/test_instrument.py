"""Tests for clarion.instrument (Wave-1b, L1 task).

Required by the subagent contract:
  (1) TimedRetriever measures real elapsed ms for a retriever with an injected
      delay (assert last_query_ms ≈ the delay, > 0).
  (2) retrieval_ms < baseline_ms with a Timed(fast fake) vs the cold baseline.
  (3) to_panel_state produces a valid PanelState whose retrieval_ms /
      baseline_ms / consent_state / grounded_facts are populated from a sample
      ClarionState.

Additional tests cover edge-cases that guard the seam's correctness:
  - TimedRetriever stamps retrieved_at on default-value (0.0) Facts.
  - SlowFakeRetriever returns grounded facts with retrieved_at > 0.
  - to_panel_state derives consent_state correctly for every decision branch.
  - to_panel_state handles an empty plan (stage → "idle").
  - to_panel_state coerces the step tuple from a list (JsonPlus round-trip).
  - to_panel_state clips trace_tail to _TRACE_TAIL events.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from clarion.contracts.events import PanelState
from clarion.contracts.state import (
    Action,
    Consent,
    Fact,
    Proposal,
    SelectorMap,
    Stage,
    TraceEvent,
)
from clarion.fakes.adapters import FakeRetriever
from clarion.instrument.baseline import COLD_RAG_BASELINE_MS, SlowFakeRetriever
from clarion.instrument.publisher import _TRACE_TAIL, to_panel_state
from clarion.instrument.timed import TimedRetriever
from clarion.kernel.graph import seed_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fact(value: str = "test-value", source_node_id: str = "node-1") -> Fact:
    return Fact(value=value, source_node_id=source_node_id, verified=True, retrieved_at=0.0)


def _make_state(**overrides):
    """Return a minimal valid ClarionState (from seed_state) with optional overrides."""
    base = seed_state(goal="pay my electric bill", mode="normal")
    base.update(overrides)  # type: ignore[arg-type]
    return base


# ---------------------------------------------------------------------------
# (1) TimedRetriever — measures real elapsed ms for an injected delay
# ---------------------------------------------------------------------------

class DelayedRetriever(FakeRetriever):
    """FakeRetriever that inserts an asyncio.sleep so TimedRetriever has
    something real to measure."""

    def __init__(self, delay_s: float) -> None:
        super().__init__()
        self._delay_s = delay_s

    async def query(self, q: str, *, k: int = 5):
        await asyncio.sleep(self._delay_s)
        return await super().query(q, k=k)


@pytest.mark.asyncio
async def test_timed_retriever_measures_elapsed_ms():
    """last_query_ms should approximate the injected delay (±50ms tolerance)."""
    delay_s = 0.05  # 50ms
    timed = TimedRetriever(DelayedRetriever(delay_s))

    assert timed.last_query_ms is None, "Should be None before first query"

    await timed.query("electric bill")

    assert timed.last_query_ms is not None
    assert timed.last_query_ms > 0
    # Allow generous ±50ms tolerance for CI scheduling jitter.
    assert abs(timed.last_query_ms - delay_s * 1000) < 50, (
        f"Expected ~{delay_s * 1000:.0f}ms, got {timed.last_query_ms:.1f}ms"
    )


@pytest.mark.asyncio
async def test_timed_retriever_stamps_retrieved_at():
    """Facts with retrieved_at == 0.0 should get a fresh Unix timestamp."""
    inner = FakeRetriever()  # always returns retrieved_at=0.0
    timed = TimedRetriever(inner)

    before = time.time()
    facts = await timed.query("some query")
    after = time.time()

    assert facts, "Should have returned at least one fact"
    for fact in facts:
        assert fact.retrieved_at >= before
        assert fact.retrieved_at <= after + 0.001  # tiny float margin


@pytest.mark.asyncio
async def test_timed_retriever_respects_existing_retrieved_at():
    """Facts whose retrieved_at is already set should NOT be overwritten."""
    sentinel_ts = 1_000_000.0
    existing_fact = Fact(
        value="already-stamped",
        source_node_id="node-x",
        retrieved_at=sentinel_ts,
    )
    inner = FakeRetriever(corpus={"sentinel": [existing_fact]})
    timed = TimedRetriever(inner)

    facts = await timed.query("sentinel query")
    # The fact with retrieved_at=sentinel_ts must be preserved.
    stamped_fact = next((f for f in facts if f.retrieved_at == sentinel_ts), None)
    assert stamped_fact is not None, "Existing retrieved_at must not be overwritten"


# ---------------------------------------------------------------------------
# (2) retrieval_ms < baseline_ms: Timed(fast fake) vs cold baseline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retrieval_ms_less_than_baseline_ms():
    """A TimedRetriever wrapping a fast fake must be noticeably cheaper
    than the cold-RAG baseline (340ms)."""
    # Fast path: FakeRetriever has essentially zero I/O latency.
    timed = TimedRetriever(FakeRetriever())
    await timed.query("electric bill")
    retrieval_ms = timed.last_query_ms

    assert retrieval_ms is not None
    assert retrieval_ms < COLD_RAG_BASELINE_MS, (
        f"Expected fast retrieval ({retrieval_ms:.2f}ms) < baseline "
        f"({COLD_RAG_BASELINE_MS}ms)"
    )


@pytest.mark.asyncio
async def test_slow_fake_retriever_returns_grounded_facts():
    """SlowFakeRetriever must return grounded Facts (source_node_id set)."""
    slow = SlowFakeRetriever(delay_ms=10)  # small delay for speed in tests
    facts = await slow.query("electric")
    assert facts, "Must return at least one fact"
    for fact in facts:
        assert fact.source_node_id is not None, "Facts from SlowFakeRetriever must be grounded"
        assert fact.retrieved_at > 0, "retrieved_at must be stamped"


@pytest.mark.asyncio
async def test_slow_fake_retriever_corpus_lookup():
    """SlowFakeRetriever uses the corpus when a needle matches."""
    corpus_fact = Fact(value="corpus-hit", source_node_id="corpus-node-1")
    slow = SlowFakeRetriever(
        delay_ms=5,
        corpus={"electric": [corpus_fact]},
    )
    facts = await slow.query("electric bill")
    assert any(f.value == "corpus-hit" for f in facts)


# ---------------------------------------------------------------------------
# (3) to_panel_state — produces a valid PanelState from a sample ClarionState
# ---------------------------------------------------------------------------

def test_to_panel_state_populates_retrieval_and_baseline():
    """PanelState must carry the caller-supplied retrieval_ms and baseline_ms."""
    state = _make_state()
    panel = to_panel_state(state, retrieval_ms=6.3, baseline_ms=COLD_RAG_BASELINE_MS)

    assert isinstance(panel, PanelState)
    assert panel.retrieval_ms == pytest.approx(6.3)
    assert panel.baseline_ms == pytest.approx(COLD_RAG_BASELINE_MS)


def test_to_panel_state_consent_state_idle_when_no_proposal():
    """consent_state must be 'idle' when there is no pending_proposal."""
    state = _make_state()
    panel = to_panel_state(state, retrieval_ms=None, baseline_ms=None)
    assert panel.consent_state == "idle"


def test_to_panel_state_consent_state_awaiting_when_proposal_no_log():
    """consent_state must be 'awaiting_yes' when proposal is set but log is empty."""
    proposal = Proposal(
        id="prop-0-0",
        utterance="Fill the amount field with 42.00. Say yes to continue.",
        action=Action(kind="fill", index=0, value="42.00"),
    )
    state = _make_state(pending_proposal=proposal)
    panel = to_panel_state(state, retrieval_ms=5.0, baseline_ms=340.0)
    assert panel.consent_state == "awaiting_yes"


def test_to_panel_state_consent_state_approved():
    """consent_state must be 'approved' when the consent_log carries an approve."""
    proposal = Proposal(
        id="prop-0-0",
        utterance="Fill the amount field.",
        action=Action(kind="fill", index=0, value="99.99"),
    )
    consent_entry = Consent(
        proposal_id="prop-0-0",
        decision="approve",
        at=time.time(),
    )
    state = _make_state(
        pending_proposal=proposal,
        consent_log=[consent_entry],
    )
    panel = to_panel_state(state, retrieval_ms=5.0, baseline_ms=340.0)
    assert panel.consent_state == "approved"


def test_to_panel_state_consent_state_rejected():
    """consent_state must be 'rejected' when the consent_log carries a reject."""
    proposal = Proposal(id="prop-0-0", utterance="Click Pay.", action=None)
    consent_entry = Consent(proposal_id="prop-0-0", decision="reject", at=time.time())
    state = _make_state(pending_proposal=proposal, consent_log=[consent_entry])
    panel = to_panel_state(state, retrieval_ms=5.0, baseline_ms=340.0)
    assert panel.consent_state == "rejected"


def test_to_panel_state_grounded_facts_populated():
    """grounded_facts must be copied verbatim from state into PanelState."""
    facts = [
        _make_fact("amount: $42.00", "node-amount"),
        _make_fact("no late fee", "node-latefee"),
    ]
    state = _make_state(grounded_facts=facts)
    panel = to_panel_state(state, retrieval_ms=4.0, baseline_ms=340.0)

    assert len(panel.grounded_facts) == 2
    assert panel.grounded_facts[0].value == "amount: $42.00"
    assert panel.grounded_facts[1].value == "no late fee"


def test_to_panel_state_stage_idle_with_empty_plan():
    """stage must be 'idle' when the plan list is empty."""
    state = _make_state(plan=[], stage_idx=0)
    panel = to_panel_state(state, retrieval_ms=None, baseline_ms=None)
    assert panel.stage == "idle"


def test_to_panel_state_stage_from_plan():
    """stage must reflect plan[stage_idx].id when a plan is present."""
    plan = [
        Stage(id="AUTH", goal="log in"),
        Stage(id="LOCATE", goal="find amount"),
    ]
    state = _make_state(plan=plan, stage_idx=1)
    panel = to_panel_state(state, retrieval_ms=None, baseline_ms=None)
    assert panel.stage == "LOCATE"


def test_to_panel_state_step_coercion():
    """step must be a tuple even when state carries a list (JsonPlus round-trip)."""
    # Simulate the JsonPlus list deserialisation by passing a list.
    state = _make_state()
    state["step"] = [2, 5]  # type: ignore[typeddict-item]
    panel = to_panel_state(state, retrieval_ms=None, baseline_ms=None)
    assert panel.step == (2, 5)
    assert isinstance(panel.step, tuple)


def test_to_panel_state_trace_tail_clipped():
    """trace_tail must contain at most _TRACE_TAIL events (most recent)."""
    events = [
        TraceEvent(node="GROUND", event="exit", at=float(i), data={"i": i})
        for i in range(_TRACE_TAIL + 10)
    ]
    state = _make_state(trace=events)
    panel = to_panel_state(state, retrieval_ms=None, baseline_ms=None)

    assert len(panel.trace_tail) == _TRACE_TAIL
    # The tail must be the LAST _TRACE_TAIL events.
    assert panel.trace_tail[0].data["i"] == 10
    assert panel.trace_tail[-1].data["i"] == _TRACE_TAIL + 9


def test_to_panel_state_proposal_utterance():
    """proposal field in PanelState must carry the pending_proposal's utterance."""
    proposal = Proposal(
        id="prop-0-0",
        utterance="I found the Amount field. I'll fill it with $55.00. Say yes.",
        action=None,
    )
    state = _make_state(pending_proposal=proposal)
    panel = to_panel_state(state, retrieval_ms=None, baseline_ms=None)
    assert panel.proposal == proposal.utterance


def test_to_panel_state_none_retrieval_and_baseline():
    """None values for retrieval_ms and baseline_ms must be preserved."""
    state = _make_state()
    panel = to_panel_state(state, retrieval_ms=None, baseline_ms=None)
    assert panel.retrieval_ms is None
    assert panel.baseline_ms is None


# ---------------------------------------------------------------------------
# Integration: TimedRetriever → to_panel_state (the full §8 data flow)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_latency_flow_into_panel_state():
    """End-to-end: timed retrieval produces retrieval_ms < baseline_ms in PanelState."""
    timed = TimedRetriever(FakeRetriever())
    facts = await timed.query("electric bill")

    state = _make_state(grounded_facts=facts)
    panel = to_panel_state(
        state,
        retrieval_ms=timed.last_query_ms,
        baseline_ms=COLD_RAG_BASELINE_MS,
    )

    assert panel.retrieval_ms is not None
    assert panel.baseline_ms is not None
    assert panel.retrieval_ms < panel.baseline_ms, (
        f"retrieval_ms ({panel.retrieval_ms:.2f}) should be < baseline_ms ({panel.baseline_ms})"
    )
    assert len(panel.grounded_facts) >= 1
    # The panel is a valid PanelState model.
    assert isinstance(panel, PanelState)
    # Serialisable (I1 will JSON-dump this for the participant attribute).
    payload = panel.model_dump_json()
    assert "retrieval_ms" in payload
    assert "baseline_ms" in payload
