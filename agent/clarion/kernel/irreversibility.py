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

# Roles that can be a consequential ACTION target (a click/navigate on one of
# these mutates the page / submits / leaves the origin). NOT a keyword list of
# names — a STRUCTURAL role set (the deleted thing was matching button *names*
# like "pay"/"submit"; this matches the a11y ROLE, which is structural).
_CONSEQUENTIAL_ROLES = {"button", "link", "menuitem", "tab", "switch"}

# State flags whose presence on the target makes the act consequential / opaque
# enough that structure can't prove it reversible. Conservative + escalate-only.
_ESCALATING_STATE_FLAGS = ("disabled", "haspopup", "expanded")

# Roles/names that read as a grounded UNDO/CANCEL affordance on the SAME page — a
# structural reversibility witness. Still NOT a keyword decider for the ACTION
# (we never match the action target's name); this only scans the page for an
# escape hatch that, if present, lets the structural net stay neutral. Absent →
# fail-closed to unknown (the UNKNOWN-on-no-grounded-undo rule).
_UNDO_NAME_TOKENS = ("cancel", "undo", "go back", "back", "previous", "edit", "discard")


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
    # The dual-signal half the model can never downgrade past. Escalate-only.
    structural = _structural_prescreen(proposal, page)

    # Either signal can ESCALATE; the model can never downgrade past the net.
    return _escalate(judgment, structural)


def _structural_prescreen(
    proposal: Proposal, page: SelectorMap
) -> Optional[Classification]:
    """The independent code-side signal — structural, NOT a name-keyword list.

    The deleted ``pay/submit/confirm/send`` matcher keyed off the action target's
    *name*; this keys off the a11y STRUCTURE the page actually exposes. Returns a
    classification to ESCALATE to (``_escalate`` only ever pushes UP the lattice —
    a structural signal can never relax the model), or ``None`` for "no structural
    opinion" (the model's judgement stands).

    Signals available on today's ``AxNode`` (``role`` / ``name`` / ``state``):
      - a NON-consequential action (``read``, or a ``fill`` into a textbox) carries
        no irreversible side-effect by itself → no opinion (``None``);
      - a consequential action (click/navigate on a button/link/…) with an EMPTY
        accessible name → escalate to ``unknown`` (a nameless consequential control
        is unidentifiable — the benignly-named "Continue" that submits, taken to
        its limit: a control with NO name at all);
      - an escalating ``state`` flag (haspopup / expanded / disabled) on the target
        → escalate to ``unknown`` (opaque / popup / unexpectedly-disabled control);
      - the FAIL-CLOSED rule the architecture endorses (Open risk #1): a
        consequential action where structure can't prove reversibility AND there is
        **no grounded undo/cancel affordance** anywhere on the page → escalate to
        ``unknown`` (UNKNOWN-on-no-grounded-undo). This over-gates — the safe
        residual — turning a confidently-wrong "reversible" into a consent prompt.

    TODO(actuator AX enrichment, later pass — do NOT reach into ``actuator/`` from
    ``kernel/`` this wave): the architecture also names ``type=="submit"`` /
    inside a ``<form>`` / off-origin navigation as strong structural escalators.
    Those signals AREN'T on ``AxNode.state`` today; a small AX enrichment in the
    actuator (stamp ``state["submit"]`` / ``state["in_form"]`` / a target origin)
    would let this pre-screen escalate a submit-like control to ``irreversible``
    (not merely ``unknown``) without a name match. Until then the
    UNKNOWN-on-no-grounded-undo net catches it (over-gating, fail-closed).
    """
    action = proposal.action
    if action is None:
        return None

    # A pure read has no side-effect; a fill into a field is reversible (you can
    # re-type). Only click/navigate are structurally consequential here.
    if action.kind not in ("click", "navigate"):
        return None

    target = _target_node(action.index, page)

    # A consequential action whose target role isn't even an actionable control is
    # structurally unidentifiable → unknown (fail-closed).
    if target is None or target.role not in _CONSEQUENTIAL_ROLES:
        return "unknown"

    # An empty accessible name on a consequential control: unidentifiable — we
    # cannot tell the user what they are about to press. Escalate.
    if not target.name.strip():
        return "unknown"

    # Opaque / popup / disabled state on the target → escalate.
    if any(target.state.get(flag) for flag in _ESCALATING_STATE_FLAGS):
        return "unknown"

    # The fail-closed net: a consequential click/navigate with NO grounded
    # undo/cancel escape hatch on the page can't be proven reversible by structure
    # → unknown (over-gates; the single worst residual, architecture Open risk #1).
    if not _has_grounded_undo(page):
        return "unknown"

    # Structure found a consequential control that IS named, not flagged opaque,
    # and sits on a page with a visible undo path — no escalation opinion; the
    # model's judgement stands (but it still can't *downgrade* anyone else's).
    return None


def _target_node(index: Optional[int], page: SelectorMap) -> Optional[AxNode]:
    """Resolve the action's target node in the live map (``None`` if no/unknown
    index)."""
    if index is None:
        return None
    return page.nodes.get(index)


def _has_grounded_undo(page: SelectorMap) -> bool:
    """Is there a structural undo/cancel/back affordance on the page? An actionable
    control (button/link/…) whose accessible name reads as an escape hatch. This is
    a structural reversibility WITNESS (its presence lets the net stay neutral); it
    is NOT a decider on the action target — we never match the target's own name."""
    for node in page.nodes.values():
        if node.role not in _CONSEQUENTIAL_ROLES:
            continue
        name = node.name.lower()
        if any(tok in name for tok in _UNDO_NAME_TOKENS):
            return True
    return False


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
