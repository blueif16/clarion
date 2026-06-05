"""The generic done-check evaluator — the SELECTED success check, evaluated in
CODE against the re-perceived page (architecture killer-closer #3).

> done is a code SELECTION: the Reasoner *selects* a registered generic check;
> CODE evaluates it against the freshly re-perceived tree (``diff_maps`` + a
> semantic anchor like a URL change or grounded status Fact). A step advances
> only on a real page-state check — never the model's say-so.

This is a NEW file AG-KERNEL exposes as a SEAM. AG-KERNEL wires the CALL
(CONFIRM/advance invokes ``evaluate_success_check``); AG-DONE hardens THIS file
(adds the semantic anchor, the SPA settling detector) and deletes the dead
``predicates.py`` DONE registry (keeping ``detect_rescue`` / RESCUE).

The five registered checks (the canonical set, shared with
``adapters.gemini_reasoner.SUCCESS_CHECKS`` and ``Subgoal.done_check``):
  - ``field_nonempty``    — a fillable target now carries a value (FILL worked).
  - ``node_added``        — the page grew a node (a confirmation/result appeared).
  - ``error_absent``      — no alert/status error + no invalid field flag.
  - ``navigated``         — the page changed (URL anchor / structural delta).
  - ``confirmation_fact`` — a grounded confirmation/success Fact is speakable.

Pure: ``clarion.contracts`` + the pure ``pipeline.diff_maps`` + the policy
``speakable`` gate. ZERO provider SDK, ZERO langgraph.
"""

from __future__ import annotations

from typing import Optional

from clarion.actuator.pipeline import diff_maps
from clarion.contracts.state import AxNode, ClarionState, SelectorMap
from clarion.kernel.policy import speakable

__all__ = ["evaluate_success_check", "SUCCESS_CHECKS"]

# The canonical registered checks (a SELECTION the Reasoner picks by name). Kept
# in lock-step with ``adapters.gemini_reasoner.SUCCESS_CHECKS``.
SUCCESS_CHECKS: tuple[str, ...] = (
    "field_nonempty",
    "node_added",
    "error_absent",
    "navigated",
    "confirmation_fact",
)

_FILLABLE_ROLES = {"textbox", "searchbox", "combobox", "spinbutton", "textarea"}
_CONFIRMATION_MARKERS = ("confirmation", "confirmed", "success", "receipt", "paid")
_ERROR_MARKERS = ("error", "invalid", "incorrect", "locked", "try again", "failed")


def evaluate_success_check(
    name: str,
    state: ClarionState,
    before_map: SelectorMap,
    after_map: SelectorMap,
    anchor: Optional[str] = None,
) -> bool:
    """Evaluate the reasoner-SELECTED generic success check in CODE.

    Args:
        name:        the registered check the Reasoner selected (∈ SUCCESS_CHECKS).
        state:       the durable ClarionState (for the grounded-fact checks).
        before_map:  the SelectorMap BEFORE the acted step (the diff baseline).
        after_map:   the freshly re-perceived SelectorMap AFTER the step.
        anchor:      a semantic anchor — the page URL at perceive time (or any
                     stable string the executor threads through). Used by
                     ``navigated`` to detect a real page change. AG-DONE upgrades
                     this into a richer semantic anchor.

    Returns True iff the named check certifies the step's page-state effect. An
    UNKNOWN / empty check name fails CLOSED (returns False) — a step never
    advances on an unrecognised or unset check (no silent always-pass).

    TODO(AG-DONE): harden — add the real semantic anchor (URL + grounded status
    Fact), an SPA settling detector so a benign poll re-render doesn't read as
    ``navigated``/``node_added``, and delete the ``predicates.py`` DONE registry
    (keep ``detect_rescue``). This is the working-but-minimal version: the five
    checks below are real (they read the diff + the live tree + grounded facts),
    just not yet settling-aware.
    """
    if name == "field_nonempty":
        return _field_nonempty(before_map, after_map)
    if name == "node_added":
        return _node_added(before_map, after_map)
    if name == "error_absent":
        return _error_absent(after_map)
    if name == "navigated":
        return _navigated(before_map, after_map, anchor)
    if name == "confirmation_fact":
        return _confirmation_fact(state, after_map)
    # Unknown / empty → fail closed (never advance on an unregistered check).
    return False


# ---------------------------------------------------------------------------
# The five checks (working-but-minimal; AG-DONE adds the semantic anchor)
# ---------------------------------------------------------------------------


def _is_filled(node: AxNode) -> bool:
    """A fillable node counts as populated if it carries a value (an explicit
    ``filled`` flag or a non-blank accessible name beyond the bare label)."""
    if node.state.get("filled") is True:
        return True
    return bool(node.name.strip())


def _field_nonempty(before: SelectorMap, after: SelectorMap) -> bool:
    """A FILL worked: some fillable field that was blank before now carries a
    value (keyed by node_id so a re-numbered index doesn't fool us). Vacuously
    False if there were no fillable fields to fill."""
    after_fillable = [
        n for n in after.nodes.values() if n.role in _FILLABLE_ROLES
    ]
    if not after_fillable:
        return False
    before_by_id = {n.node_id: n for n in before.nodes.values()}
    for n in after_fillable:
        was = before_by_id.get(n.node_id)
        # newly-filled (blank→value) OR a fillable field that is now non-empty.
        if _is_filled(n) and (was is None or not _is_filled(was)):
            return True
    return False


def _node_added(before: SelectorMap, after: SelectorMap) -> bool:
    """The page grew a node — a result/confirmation/error surfaced. Uses the
    shared pure ``diff_maps`` (identity = role+name+node_id)."""
    return bool(diff_maps(before, after).added)


def _error_absent(after: SelectorMap) -> bool:
    """No surfaced error: no alert/status node naming an error AND no fillable
    field carrying an ``invalid`` flag (the silent validation error the screen
    reader never announced)."""
    for n in after.nodes.values():
        if n.role in ("alert", "status") and any(
            m in n.name.lower() for m in _ERROR_MARKERS
        ):
            return False
        if n.state.get("invalid") is True:
            return False
    return True


def _navigated(
    before: SelectorMap, after: SelectorMap, anchor: Optional[str]
) -> bool:
    """The page changed. The semantic anchor (URL at perceive time) is the
    primary signal when threaded; absent it, a STRUCTURAL change (nodes added or
    removed) stands in. AG-DONE makes this anchor-first + settling-aware."""
    if anchor:
        # The executor threads the *current* url as the anchor; a real nav makes
        # the structural fingerprint move. Until AG-DONE adds before/after URL
        # capture, fall through to the structural delta below (anchor presence
        # alone is not proof of a change).
        pass
    diff = diff_maps(before, after)
    return bool(diff.added or diff.removed)


def _confirmation_fact(state: ClarionState, after: SelectorMap) -> bool:
    """A grounded confirmation/success Fact is speakable (grounded AND verified)
    OR a confirmation marker is present in the re-perceived tree. The grounded
    fact is the strong signal — the page marker is the fallback so a read-only
    lookup that surfaced a status line still certifies."""
    for f in speakable(state.get("grounded_facts", []) or []):
        if any(m in f.value.lower() for m in _CONFIRMATION_MARKERS):
            return True
    for n in after.nodes.values():
        if any(m in n.name.lower() for m in _CONFIRMATION_MARKERS):
            return True
    return False
