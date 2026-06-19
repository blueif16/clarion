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

# Roles whose click is RE-SELECTABLE — clicking one selects/toggles a state the user
# can put back by clicking the alternative again (a "multiple choice": radio /
# checkbox / switch / tab / menuitem and their menu variants). A STRUCTURAL role set
# (the de-hardcoding rule), NOT a name match. These are reversible by OUR OWN
# capability (we just re-click), so they are exempt from the fail-closed no-undo net.
_REVERSIBLE_CLICK_ROLES = {
    "radio",
    "checkbox",
    "switch",
    "tab",
    "menuitem",
    "menuitemradio",
    "menuitemcheckbox",
    "option",
}


def _is_reversible_by_capability(
    proposal: Proposal, page: SelectorMap
) -> bool:
    """True iff WE can put this action back ourselves, regardless of any on-page
    cancel affordance — the "can we undo it?" axis, grounded on ``kind`` + the a11y
    ``role`` (NEVER a name match):

      - a NAVIGATE, or a CLICK on a LINK (``<a href>``): navigation. We record
        ``url_before`` and re-navigate to it — a redirect is "no big deal"; browser-
        back is the undo. (The reasoner navigates by clicking links, not by emitting
        navigate actions, so the link case is the COMMON redirect path — without it
        every "take me to X" read as ``unknown`` and got the wrong "treat-as-final".)
      - a CLICK on a RE-SELECTABLE control (radio/checkbox/switch/tab/menuitem — a
        "multiple choice"): re-clicking the alternative reverts the selection.

    Used by BOTH the structural pre-screen (don't ESCALATE these for lacking an
    on-page undo) and ``classify`` (DOWNGRADE the conservative ``unknown`` for these,
    since the no-undo net is what made the model hedge ``unknown`` in the first
    place). The model's CONFIDENT ``irreversible`` is still honoured in ``classify``
    — only the ``unknown`` is relaxed."""
    action = proposal.action
    if action is None:
        return False
    if action.kind == "navigate":
        return True
    if action.kind == "click":
        target = _target_node(action.index, page)
        if target is not None and (
            target.role == "link" or target.role in _REVERSIBLE_CLICK_ROLES
        ):
            return True
    # A bare FILL commits nothing — the native setter types into a field we can
    # re-type or clear (the submit is the commit, not the typing), so it is
    # reversible by our own capability. NOT exempt when ``submit`` is set: a
    # submitting fill presses Enter and commits the query — consequential, treated
    # like a click by the structural net. (The live 06-11 run gated a search-box
    # fill as "treat as final" — the model hedged ``unknown`` because the page has
    # no on-screen undo, which is exactly the conservative hedge this capability
    # axis exists to relax.)
    if action.kind == "fill" and not action.submit:
        return True
    return False


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
    # A read-back performs NO mutation, so it is reversible BY CONSTRUCTION —
    # whatever the model judged. PROPOSE degrades an off-page / value-less step to
    # a side-effect-free ``read`` while still carrying the model's irreversibility
    # judgement of the ABANDONED step on ``pending_step``; that stale judgement must
    # NOT flag the read irreversible. If it does, ``consent_gate`` (which auto-
    # proceeds a read — no side-effect to gate) routes it straight to ACT and
    # ``assert_consented`` then hard-stops a harmless grounded read with a
    # PolicyViolation. foundation §5 gates consequential ACTS, never a read — so
    # this structural truth lives HERE in the classifier, not bolted onto the graph.
    if proposal.action is not None and proposal.action.kind == "read":
        return "reversible"

    judgment: Classification = (
        reasoner_judgment
        if reasoner_judgment in ("reversible", "irreversible", "unknown")
        else "unknown"
    )

    # CAPABILITY DOWNGRADE (the fix for "every navigation got a treat-as-final
    # consent"): a move WE can put back ourselves — a navigate / link-click (re-
    # navigate to url_before) or a re-selectable click (re-click) — is reversible by
    # construction. The model's conservative ``unknown`` (it hedges because the page
    # has no on-screen undo) is therefore relaxed to ``reversible`` so navigation
    # flows without a consent stop. A CONFIDENT ``irreversible`` is NEVER relaxed —
    # if the model judged the link commits something final, it still gates; and a
    # bare button that might submit is NOT capability-reversible, so it stays gated
    # by the structural net below. The agentic invariant is intact.
    if judgment != "irreversible" and _is_reversible_by_capability(proposal, page):
        return "reversible"

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

    # A pure read has no side-effect; a BARE fill into a field is reversible (you
    # can re-type). Only click/navigate — and a SUBMITTING fill (``submit=True``
    # presses Enter, committing the query like a button press) — are structurally
    # consequential here.
    if action.kind == "fill" and action.submit:
        # Fall through to the net below. Its target is an entry field (textbox/
        # combobox), not a _CONSEQUENTIAL_ROLES control, so the role check yields
        # ``unknown`` → a submitting fill ALWAYS gates (fail-closed: we cannot
        # structurally prove what the Enter commits).
        pass
    elif action.kind not in ("click", "navigate"):
        return None

    target = _target_node(action.index, page)

    # CAPABILITY-AWARE EXEMPTION (the "can WE put it back?" axis). A navigate /
    # link-click / re-selectable click is reversible by OUR OWN capability,
    # regardless of any on-page cancel affordance, so the fail-closed no-undo net
    # below must not force it to ``unknown`` (``classify`` also DOWNGRADES these from
    # the model's conservative ``unknown``). One source of truth for the role/kind
    # conditions; ``None`` = no structural escalation → the model's judgement stands
    # (escalate-only intact: a model ``irreversible`` is never relaxed, and a bare
    # button that might submit is NOT exempt, so the commit net below still gates it).
    if _is_reversible_by_capability(proposal, page):
        return None

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
