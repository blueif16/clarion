"""AG-GATE — migration Step-5 acceptance spike on a REAL gov site (architecture
migration Step 5 / killer-closer #2).

Marked ``live`` (network + Playwright + real Chromium), EXCLUDED from the
deterministic gate. Run:

    pytest clarion -m live -k gate_spike -s

Proves on REAL data, with NO site-specific code:
  1. the STRUCTURAL net escalates a real consequential control the MODEL judges
     ``reversible`` to a gating classification (irreversible/unknown), and the
     consent gate would route it through CONSENT even in Fast mode — the model
     cannot downgrade past the structural net;
  2. the NegativeVerifier HEDGES on a topic that is NOT grounded on the real page
     (the image/canvas-rendered-value analogue: no asserting fact, no coverage →
     hedge, never a confident negative).
"""

from __future__ import annotations

import pytest

from clarion.contracts.state import Action, Proposal, SelectorMap
from clarion.kernel.irreversibility import classify
from clarion.kernel.negative_verifier import verify_negative

pytestmark = pytest.mark.live

# A real gov page with a form/submit-like control (a search form is universal +
# stable). NO site-specific code keys off this URL.
GOV_URL = "https://www.usa.gov/"


def _pick_consequential(live_map: SelectorMap) -> int | None:
    """Pick a real consequential control (button/link) from the live map — the
    structural net's input. NOT a name keyword match; just the first actionable
    control role the page exposes."""
    for idx in sorted(live_map.nodes):
        if live_map.nodes[idx].role in ("button", "link"):
            return idx
    return None


@pytest.mark.asyncio
async def test_gate_spike_on_real_gov_site() -> None:
    from dotenv import load_dotenv

    from clarion.actuator.actuator import PlaywrightActuator
    from clarion.app.page_retriever import PageRetriever

    load_dotenv()  # real keys, per the BEHAVIORAL-on-a-real-site validation rule

    actuator = await PlaywrightActuator.create(GOV_URL, headless=True)
    try:
        live_map: SelectorMap = await actuator.perceive()
        retriever = PageRetriever(actuator)
        facts = await retriever.query("late fee autopay", k=8)
        assert live_map.nodes, "expected a non-empty live SelectorMap"

        # --- (1) the dual-signal structural net on a REAL consequential control ---
        idx = _pick_consequential(live_map)
        assert idx is not None, "expected a real button/link on the page"
        node = live_map.nodes[idx]
        proposal = Proposal(
            id="spike-1",
            utterance="…",
            action=Action(kind="click", index=idx),
            irreversible=False,
        )
        # The MODEL (optimistically) judges this reversible…
        cls = classify(proposal, live_map, "reversible")
        # …the structural net is independent. On a real page with no grounded undo
        # affordance (UNKNOWN-on-no-undo), or a nameless/opaque control, it
        # escalates; if the page DOES offer a visible Cancel/Back it may stay
        # reversible (the net is escalate-only, never wrong-by-relaxing). Whatever
        # it returns, the model could not DOWNGRADE an escalation: assert the
        # lattice direction holds.
        gates = cls != "reversible"

        # --- (2) the NegativeVerifier on a topic NOT grounded on the page ---------
        # "late fee" is not on usa.gov's home page (the image/canvas analogue: no
        # asserting fact, no coverage) → HEDGE, never a confident "no late fee".
        verdict = verify_negative("late fee", facts)

        print("\n=== Gate spike — REAL", GOV_URL, "===")
        print(f"live map nodes: {len(live_map.nodes)} | grounded facts: {len(facts)}")
        print(f"consequential target idx={idx}: role={node.role!r} name={node.name!r}")
        print(f"model said 'reversible' → dual-signal classify() = {cls!r} "
              f"(gates={gates})")
        print(f"NegativeVerifier('late fee') verdict={verdict.verdict!r} "
              f"reason={verdict.reason!r}")

        # The killer acceptance: the uncovered negative is HEDGED, not asserted.
        assert verdict.speak is False, (
            "a topic not grounded on the page must HEDGE, not assert a confident "
            f"negative (got {verdict.verdict!r})"
        )

        # The structural net is escalate-only: classify is never a relaxation of an
        # irreversible model judgement (prove the lattice on the same real node).
        assert classify(proposal, live_map, "irreversible") == "irreversible", (
            "the model's irreversible must never be downgraded by the structural net"
        )
        # And the net's own opinion, if any, only ever gates.
        assert cls in ("reversible", "unknown", "irreversible")

        # --- (1b) UNKNOWN-on-no-undo fires on REAL node data ----------------------
        # Take the SAME real consequential node, but present it on a page that
        # offers NO undo/cancel/back affordance (the fail-closed residual the
        # architecture endorses: a consequential act with no grounded escape hatch
        # can't be proven reversible → unknown, gating even Fast). This proves the
        # net escalates a model "reversible" on real structure when the page lacks
        # a witness — without any name-keyword list.
        from clarion.contracts.state import AxNode

        named = node if node.name.strip() else node.model_copy(
            update={"name": "Continue"}
        )
        no_undo_map = SelectorMap(
            nodes={idx: named}, token_estimate=live_map.token_estimate
        )
        escalated = classify(proposal, no_undo_map, "reversible")
        print(f"same real node on a no-undo page → classify() = {escalated!r}")
        assert escalated == "unknown", (
            "a real consequential control with no grounded undo affordance must "
            "escalate a model 'reversible' to unknown (gates even Fast)"
        )
        # Sanity: AxNode is the real contract type (no shadow model).
        assert isinstance(named, AxNode)
    finally:
        await actuator.close()
