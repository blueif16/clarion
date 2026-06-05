"""The IrreversibilityGate classifier — the dual-signal ``{reversible |
irreversible | unknown}`` decision (architecture killer-closer #2).

> a benignly-named "Continue" that submits → **dual-signal gate**: the model
> judges from grounded context AND an independent **code structural pre-screen**
> runs at the gate. **Either signal can escalate; the model can never downgrade
> past the structural net.** UNKNOWN routes through CONSENT even in Fast mode.

This is a NEW file AG-KERNEL exposes as a SEAM: the kernel graph wires the gate
node + routes consent on this output. AG-GATE hardens THIS file (the structural
pre-screen + the NegativeVerifier in ``kernel/negative_verifier.py`` +
UNKNOWN-gates-Fast). AG-KERNEL's version is WORKING-but-minimal: it returns the
reasoner's judgement so the routing is exercised end-to-end on real data, and it
leaves the structural net as a clearly-marked TODO.

Pure: ``clarion.contracts`` + stdlib only. ZERO provider SDK (the model judgement
arrives pre-decided as a string — the Gemini SDK never enters ``kernel/``).
"""

from __future__ import annotations

from typing import Literal, Optional

from clarion.contracts.state import AxNode, Proposal, SelectorMap

Classification = Literal["reversible", "irreversible", "unknown"]

__all__ = ["classify", "Classification"]


def classify(
    proposal: Proposal,
    page: SelectorMap,
    reasoner_judgment: Classification,
) -> Classification:
    """Classify a proposed action's reversibility from TWO independent signals.

    Contract (the part AG-KERNEL freezes for the routing): returns one of
    ``"reversible" | "irreversible" | "unknown"``. The kernel routes
    ``irreversible`` OR ``unknown`` → CONSENT (even in Fast mode);
    ``reversible`` → Fast auto-proceeds.

    AG-KERNEL minimal body: trust the reasoner's judgement so the wiring is
    exercised on real data. The structural net below is a NO-OP stub today — but
    it is already positioned so AG-GATE only edits THIS function (and adds
    ``kernel/negative_verifier.py``), never the graph.

    Fail-closed default: an empty / unrecognised judgement is treated as
    ``"unknown"`` (which gates), never silently ``"reversible"``.
    """
    judgment: Classification = (
        reasoner_judgment
        if reasoner_judgment in ("reversible", "irreversible", "unknown")
        else "unknown"
    )

    # --- the independent code structural pre-screen (the "net") --------------
    # TODO(AG-GATE): implement the real structural pre-screen here. It must be
    # able to ESCALATE the model's judgement but NEVER downgrade it:
    #   - role=="button" AND (type==submit | inside a <form>)        → escalate
    #   - off-origin navigation target                                → escalate
    #   - an empty accessible name on a consequential control         → escalate
    #   - no grounded "undo"/"cancel" affordance present              → → unknown
    # Pair with NegativeVerifier (kernel/negative_verifier.py) for the
    # coverage-aware "no undo afforded" signal. Until then this is a no-op so the
    # routing runs on the reasoner's signal alone.
    structural = _structural_prescreen(proposal, page)  # currently always None

    # Either signal can ESCALATE; the model can never downgrade past the net.
    return _escalate(judgment, structural)


def _structural_prescreen(
    proposal: Proposal, page: SelectorMap
) -> Optional[Classification]:
    """The independent code-side signal. AG-KERNEL stub: returns ``None`` (no
    structural opinion) so today's routing rides on the reasoner's judgement.
    AG-GATE fills this in (see the TODO in ``classify``). Kept a separate pure fn
    so AG-GATE's edit is localized and the escalation lattice (`_escalate`) is
    already in place."""
    _ = (proposal, page)
    return None


# The escalation lattice: how strongly each label gates (higher = more gating).
_RANK: dict[Classification, int] = {"reversible": 0, "unknown": 1, "irreversible": 2}


def _escalate(
    model: Classification, structural: Optional[Classification]
) -> Classification:
    """Combine the two signals: take the MORE-gating of the two (the structural
    net can only push UP the lattice; it can never relax the model). ``None``
    structural means "no independent opinion" → the model's judgement stands."""
    if structural is None:
        return model
    return model if _RANK[model] >= _RANK[structural] else structural
