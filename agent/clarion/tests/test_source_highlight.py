"""Source-node highlight — the epistemic-clause proof surface (companion to the
action-trace feed). These tests pin the load-bearing guarantees WITHOUT a browser:

  A. ``cdp_highlight_by_backend`` is NODE-IDENTITY-driven and never coordinate/bbox
     driven — it resolves the SAME ``backendDOMNodeId`` the click uses and reads the
     node's LIVE geometry inside the injected function; it never sends an x/y/w/h.
  B. ``cdp_clear_highlight`` removes the overlay via a bare ``Runtime.evaluate`` and
     both helpers FAIL-OPEN (the product never depends on the highlight).
  C. The kernel ``_source_ref`` projection carries the field's identity + its PROVEN
     paired label, and returns ``None`` for a read-back / clarify.
  D. The ``StageGraphRunner`` draws the field + mirrors the field⟷label panel row at
     a parked consent, and shows the two-sided "verified absent" row at END.
"""

from __future__ import annotations

import json

import pytest

from clarion.actuator.pipeline import cdp_clear_highlight, cdp_highlight_by_backend
from clarion.contracts.events import ConsentRequest, SourceRef
from clarion.contracts.state import (
    Action,
    AxNode,
    Fact,
    PairedFact,
    Proposal,
    SelectorMap,
    StepProposal,
)
from clarion.kernel.graph import _source_ref


# ---------------------------------------------------------------------------
# A + B — the shared CDP draw routine (no browser; a recording ``send``).
# ---------------------------------------------------------------------------


def _recorder():
    sent: list[tuple[str, dict]] = []

    async def send(method: str, params: dict | None = None) -> dict:
        sent.append((method, params or {}))
        if method == "DOM.resolveNode":
            return {"object": {"objectId": "obj-xyz"}}
        return {}

    return send, sent


@pytest.mark.asyncio
async def test_highlight_is_node_identity_never_bbox():
    """(A) Highlight resolves the SAME backendDOMNodeId the click uses, draws via
    callFunctionOn on the resolved object (reading LIVE getBoundingClientRect), and
    NEVER sends a coordinate/bbox to the browser."""
    send, sent = _recorder()
    ok, _detail = await cdp_highlight_by_backend(send, 4242)
    assert ok is True

    # Resolved BY node identity — the backend id, never a point.
    resolve_p = next(p for m, p in sent if m == "DOM.resolveNode")
    assert resolve_p["backendNodeId"] == 4242
    scroll_p = next(p for m, p in sent if m == "DOM.scrollIntoViewIfNeeded")
    assert scroll_p["backendNodeId"] == 4242
    # Drawn on the resolved object; geometry read LIVE inside the injected function.
    cfo = next(p for m, p in sent if m == "Runtime.callFunctionOn")
    assert cfo["objectId"] == "obj-xyz"
    assert "getBoundingClientRect" in cfo["functionDeclaration"]
    # NEVER a stored bbox / coordinate on the wire, and no synthetic mouse event.
    methods = [m for m, _ in sent]
    assert "Input.dispatchMouseEvent" not in methods
    for _m, p in sent:
        for banned in ("x", "y", "width", "height", "bbox", "quads"):
            assert banned not in p
    # And nothing in the traffic carries a numeric coordinate payload.
    assert "bbox" not in json.dumps(sent)


@pytest.mark.asyncio
async def test_clear_uses_runtime_evaluate():
    """(B) Clear removes the overlay by id via a bare Runtime.evaluate (no node)."""
    send, sent = _recorder()
    await cdp_clear_highlight(send)
    evals = [p for m, p in sent if m == "Runtime.evaluate"]
    assert evals and "__clarion_src_hi__" in evals[0]["expression"]


@pytest.mark.asyncio
async def test_helpers_fail_open():
    """(B) A transport error never raises into the turn — highlight returns (False,
    …) and clear swallows."""

    async def boom(method: str, params: dict | None = None) -> dict:
        raise RuntimeError("relay down")

    ok, _detail = await cdp_highlight_by_backend(boom, 1)
    assert ok is False
    await cdp_clear_highlight(boom)  # must not raise


# ---------------------------------------------------------------------------
# C — the kernel projection (field identity + PROVEN paired label).
# ---------------------------------------------------------------------------


def test_source_ref_fill_carries_field_and_paired_label():
    """(C) A fill step yields the field index/node_id/name + the PROVEN label (the
    PairedFact whose VALUE half IS this field) + the structural method."""
    field = AxNode(index=3, role="textbox", name="Card number", node_id="ax-23")
    page = SelectorMap(nodes={3: field})
    pair = PairedFact(
        label=Fact(value="Card number", source_node_id="ax-22", verified=True),
        value=Fact(value="", source_node_id="ax-23", verified=True),
        method="for",
    )
    state = {"page_index": page, "paired_facts": [pair]}
    proposal = Proposal(
        id="prop-0-0",
        utterance="I found the Card number field…",
        action=Action(kind="fill", index=3, value="4821"),
    )
    src = _source_ref(state, proposal)  # type: ignore[arg-type]
    assert src == SourceRef(
        index=3,
        node_id="ax-23",
        name="Card number",
        label_text="Card number",
        method="for",
    )


def test_source_ref_click_without_pairing():
    """(C) A click with no backing pairing still carries the field identity; the
    label/method are simply empty (never guessed)."""
    link = AxNode(index=1, role="link", name="Apply for benefits", node_id="ax-9")
    state = {"page_index": SelectorMap(nodes={1: link}), "paired_facts": []}
    proposal = Proposal(
        id="p", utterance="…", action=Action(kind="click", index=1)
    )
    src = _source_ref(state, proposal)  # type: ignore[arg-type]
    assert src is not None
    assert (src.index, src.node_id, src.name) == (1, "ax-9", "Apply for benefits")
    assert src.label_text == "" and src.method == ""


def test_source_ref_read_is_none():
    """(C) A read-back / clarify has no actionable source node → no highlight."""
    state = {"page_index": SelectorMap(nodes={}), "paired_facts": []}
    proposal = Proposal(
        id="p", utterance="Here is what I found…",
        action=Action(kind="read", index=None),
    )
    assert _source_ref(state, proposal) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# D — the runner wiring (draw + panel row at consent; absent row at END).
# ---------------------------------------------------------------------------


class _FakeActuator:
    def __init__(self) -> None:
        self.highlighted: list[int] = []
        self.clears = 0

    async def highlight(self, source_index: int) -> None:
        self.highlighted.append(source_index)

    async def clear_highlight(self) -> None:
        self.clears += 1


class _FakeRuntime:
    def __init__(self) -> None:
        self.actuator = _FakeActuator()


def _runner_with(actuator_runtime):
    pytest.importorskip("livekit")  # voice_entry imports the livekit plugins
    from clarion.app.voice_entry import StageGraphRunner

    runner = StageGraphRunner(runtime=None)
    runner._graph = object()  # mark ready without building a real graph
    runner._runtime = actuator_runtime
    return runner


@pytest.mark.asyncio
async def test_runner_draws_field_and_emits_pair_row():
    """(D) At a parked consent the runner clears the prior box, draws the field by
    its index, and emits the field⟷label panel row on the [source] HUD line."""
    rt = _FakeRuntime()
    runner = _runner_with(rt)
    rows: list[tuple] = []
    runner._hud_sink = lambda phase, detail, level: rows.append((phase, detail, level))
    req = ConsentRequest(
        proposal_id="prop-0-0",
        utterance="I found the Card number field…",
        source=SourceRef(
            index=3, node_id="ax-23", name="Card number",
            label_text="Card number", method="for",
        ),
    )
    await runner._apply_highlight(req)
    assert rt.actuator.highlighted == [3]
    assert rt.actuator.clears == 1  # prior box cleared first (fade-on-next-step)
    assert rows and rows[0][0] == "[source]"
    assert "Card number" in rows[0][1] and "via for" in rows[0][1] and "node ax-23" in rows[0][1]


@pytest.mark.asyncio
async def test_runner_absent_shows_two_sided_empty_state():
    """(D) The two-sided proof: a verified ABSENCE clears the box and shows the
    'verified absent — nothing to point at' row (the move a screenshot can't copy)."""
    rt = _FakeRuntime()
    runner = _runner_with(rt)
    rows: list[tuple] = []
    runner._hud_sink = lambda phase, detail, level: rows.append((phase, detail, level))
    runner._last_values = {
        "pending_step": StepProposal(action_kind="read", asserts_absence=True)
    }
    await runner._apply_highlight_end()
    assert rt.actuator.clears == 1
    assert any("verified absent" in d for _p, d, _l in rows)


@pytest.mark.asyncio
async def test_runner_no_source_clears_only():
    """(D) A consent with no source node (a read-back/clarify) clears any prior box
    and draws nothing — never guesses a target."""
    rt = _FakeRuntime()
    runner = _runner_with(rt)
    rows: list[tuple] = []
    runner._hud_sink = lambda phase, detail, level: rows.append((phase, detail, level))
    req = ConsentRequest(proposal_id="p", utterance="which did you mean?", source=None)
    await runner._apply_highlight(req)
    assert rt.actuator.highlighted == []
    assert rt.actuator.clears == 1
    assert rows == []
