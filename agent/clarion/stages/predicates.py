"""RESCUE detection — the screen-reader-choked-widget cross-cut (KEPT).

The hardcoded done-predicate / negative-check REGISTRY that used to live here
(``DONE_PREDICATES`` / ``NEGATIVE_CHECKS`` / ``stage_advances`` + the per-stage
``auth_done`` / ``locate_done`` / ``fill_done`` / … predicates + the
``_LOGGED_IN_MARKERS`` / ``_AUTOPAY_MARKERS`` / ``_FEE_UPSELL_MARKERS`` marker
lists) is **DELETED** (architecture migration Step 4): "done" is no longer a
per-stage hardcoded predicate baked to the AUTH→…→CONFIRM pay topology. It is now
a code SELECTION the Reasoner makes — a *generic* registered check
(``field_nonempty`` / ``node_added`` / ``error_absent`` / ``navigated`` /
``confirmation_fact``) that CODE evaluates against the re-perceived tree + a
semantic anchor (killer-closer #3) in ``stages.checks.evaluate_success_check``.
The generic executor (``stages.graph``) calls that, never ``stage_advances``.

What stays here is RESCUE — a cross-cutting detection (NOT a stage) that any step
runs against the current ``SelectorMap`` (foundation §4: the most-validated
trigger, Aira 62%). ``detect_rescue`` flags a "screen-reader-choked" widget (an
interactive AXTree node with a role but an EMPTY accessible name, or a focus-trap)
so the executor can branch to a rescue sub-flow and return. RESCUE is generic and
site-agnostic — exactly the property the deleted done-registry lacked.

Pure: ``clarion.contracts`` only. NO provider SDKs, NO langgraph.
"""

from __future__ import annotations

from clarion.contracts.state import AxNode, SelectorMap

# Interactive roles whose EMPTY accessible name means the screen reader has
# nothing to announce — the RESCUE trigger (execution §3.2 note, foundation §4:
# the most-validated trigger, Aira 62%). A *structural* node with an empty name
# (a generic/div/group) is fine; an interactive control with no name is the bug.
_INTERACTIVE_ROLES_NEEDING_NAME = {
    "textbox",
    "searchbox",
    "button",
    "link",
    "checkbox",
    "radio",
    "combobox",
    "listbox",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "tab",
    "switch",
    "slider",
    "spinbutton",
    "textarea",
}


# ---------------------------------------------------------------------------
# RESCUE detection (cross-cutting; execution §3 note, foundation §4)
# ---------------------------------------------------------------------------


def is_choked_widget(node: AxNode) -> bool:
    """A "screen-reader-choked" widget: an interactive control the screen reader
    cannot announce.

    Two heuristics (execution §3 note):
      1. **Empty accessible name** — an interactive role (button, textbox, …) with
         no accessible name. The screen reader reaches it and has nothing to say.
      2. **Focus-trap** — the node is focused but disabled/aria-hidden, or marked
         with an explicit focus-trap flag: focus lands and cannot move on.
    """
    role = node.role
    if role not in _INTERACTIVE_ROLES_NEEDING_NAME:
        return False
    # (1) empty accessible name on an interactive control.
    if not node.name.strip():
        return True
    # (2) focus-trap: focused but hidden/disabled, or explicitly flagged.
    if node.state.get("focus_trap") is True:
        return True
    if node.state.get("focused") is True and (
        node.state.get("hidden") is True or node.state.get("disabled") is True
    ):
        return True
    return False


def detect_rescue(sm: SelectorMap) -> list[AxNode]:
    """Return every choked widget in the current SelectorMap. A non-empty result
    is the RESCUE trigger: the executor graph branches to the rescue sub-flow, then
    returns to the interrupted step (execution §3 note)."""
    return [n for n in sm.nodes.values() if is_choked_widget(n)]


def needs_rescue(sm: SelectorMap) -> bool:
    """True iff any widget in the current tree chokes the screen reader."""
    return any(is_choked_widget(n) for n in sm.nodes.values())


__all__ = [
    "is_choked_widget",
    "detect_rescue",
    "needs_rescue",
]
