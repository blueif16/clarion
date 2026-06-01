"""ST1 — the done-predicate / negative-check registry (execution §3.3).

"Done" is never vibes (execution §3.3 / Reddit's 6-month ops lesson). Every stage
carries a **machine-checkable** done-predicate AND a negative-verification list,
both evaluated against the freshly re-perceived ``SelectorMap`` (and the durable
``ClarionState``) in CONFIRM. A stage cannot advance on the model's say-so alone.

This module is the registry of those checks. Each fn has the signature::

    (state: ClarionState, selector_map: SelectorMap) -> bool

A done-predicate returns ``True`` when the stage's goal is *provably* reached
against the live tree. A negative-check returns ``True`` when the negative
condition it names is **satisfied** (i.e. the bad state is absent / the page is
safe) — so a stage advances iff its done-predicate is True AND every negative
check is True.

Pure: imports only ``clarion.contracts`` + the kernel policy (``speakable`` — the
epistemic gate). NO provider SDKs, NO langgraph. The stage graph (``graph.py``)
resolves the names a ``Stage`` carries (``Stage.done_predicate`` /
``Stage.negative_checks``) through ``DONE_PREDICATES`` / ``NEGATIVE_CHECKS`` here.

RESCUE (execution §3.107 / §3.2 note) is *not* a stage — it is a cross-cutting
detection that any stage runs against the current ``SelectorMap``:
``detect_rescue`` flags a "screen-reader-choked" widget (an interactive AXTree
node with a role but an EMPTY accessible name, or a focus-trap) so the graph can
branch to a rescue sub-flow and return.
"""

from __future__ import annotations

from typing import Callable

from clarion.contracts.state import AxNode, ClarionState, SelectorMap
from clarion.kernel.policy import speakable

# A registered check: pure fn of (state, selector_map) -> bool.
Predicate = Callable[[ClarionState, SelectorMap], bool]

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

# State flags that mark a fillable field as required (different a11y stacks expose
# this differently; we read any of them).
_REQUIRED_FLAGS = ("required", "aria-required")

# Fillable roles — what FILL must populate.
_FILLABLE_ROLES = {"textbox", "searchbox", "combobox", "spinbutton", "textarea"}

# Markers (substring, case-insensitive) of a logged-in state in an AX name.
_LOGGED_IN_MARKERS = ("log out", "logout", "sign out", "my account", "dashboard")
# Error / locked banners — the AUTH negative check.
_ERROR_MARKERS = ("error", "invalid", "incorrect", "locked", "try again", "failed")
# A confirmation / success marker — CONFIRM's done-predicate substrate.
_CONFIRMATION_MARKERS = ("confirmation", "confirmed", "success", "receipt", "paid")
# An autopay-already-scheduled state LOCATE must not duplicate.
_AUTOPAY_MARKERS = ("autopay is on", "autopay enabled", "automatic payments are on")
# A surprise fee / upsell REVIEW must not let through.
_FEE_UPSELL_MARKERS = ("convenience fee", "processing fee", "upsell", "add autopay")


# ---------------------------------------------------------------------------
# Small helpers over the SelectorMap (pure)
# ---------------------------------------------------------------------------


def _is_filled(node: AxNode) -> bool:
    """A fillable node counts as populated if it carries a non-empty value.

    The actuator surfaces the live ``.value`` either as the node ``name`` (the
    fake's native-setter analogue records ``"Amount: $42.00"``) or as a
    ``state['filled']`` flag. We treat a non-blank name beyond the bare label, or
    an explicit filled flag, as populated. The conservative read: a textbox whose
    name is empty or equals only its label is NOT filled.
    """
    if node.state.get("filled") is True:
        return True
    # A value-bearing name: the fake records "Amount: $42.00"; a real fill makes
    # the input's accessible value non-empty. An empty name is definitely blank.
    return bool(node.name.strip())


def _is_required(node: AxNode) -> bool:
    return any(node.state.get(flag) is True for flag in _REQUIRED_FLAGS)


def _required_fields(sm: SelectorMap) -> list[AxNode]:
    return [
        n
        for n in sm.nodes.values()
        if n.role in _FILLABLE_ROLES and _is_required(n)
    ]


def _any_name_contains(sm: SelectorMap, markers: tuple[str, ...]) -> bool:
    for n in sm.nodes.values():
        name = n.name.lower()
        if any(m in name for m in markers):
            return True
    return False


def _has_validation_error(sm: SelectorMap) -> bool:
    """A silent validation error: an ``alert``/``status`` node naming an error, OR
    a fillable field carrying an ``invalid`` state flag (the one the screen reader
    never announced — execution §3.2 FILL negative)."""
    for n in sm.nodes.values():
        if n.role in ("alert", "status") and any(
            m in n.name.lower() for m in _ERROR_MARKERS
        ):
            return True
        if n.state.get("invalid") is True:
            return True
    return False


# ---------------------------------------------------------------------------
# Done-predicates (execution §3.2 "Done-predicate" column)
# ---------------------------------------------------------------------------


def auth_done(state: ClarionState, sm: SelectorMap) -> bool:
    """AUTH done: a logged-in marker is present in the AXTree."""
    return _any_name_contains(sm, _LOGGED_IN_MARKERS)


def locate_done(state: ClarionState, sm: SelectorMap) -> bool:
    """LOCATE done: amount + payee + due-date all grounded with source nodes.

    Machine check, not say-so: we require >= 3 *speakable* (grounded AND verified)
    facts in state — the epistemic clause supplies the grounding, the policy's
    ``speakable`` enforces it. (The hero task needs amount, payee, due-date.)
    """
    return len(speakable(state["grounded_facts"])) >= 3


def fill_done(state: ClarionState, sm: SelectorMap) -> bool:
    """FILL done: ALL required goal-fields are populated.

    The blank-required-field case returns False — the stage cannot advance
    (execution §15 ST1 accept #2). A page with no required fields is vacuously
    done (nothing to fill)."""
    required = _required_fields(sm)
    return all(_is_filled(n) for n in required)


def review_done(state: ClarionState, sm: SelectorMap) -> bool:
    """REVIEW done: the amount we're about to pay matches a known/grounded balance.

    Machine check: at least one speakable amount fact exists AND it appears on the
    page (cross-checked against the live tree), i.e. what we verified is what the
    page shows. Conservative: requires a grounded amount to cross-check."""
    sayable = speakable(state["grounded_facts"])
    amounts = [f.value for f in sayable if "$" in f.value or "amount" in f.value.lower()]
    if not amounts:
        return False
    # Cross-check: the verified amount string is present somewhere in the tree.
    page_text = " ".join(n.name for n in sm.nodes.values()).lower()
    return any(a.lower() in page_text for a in amounts)


def pay_done(state: ClarionState, sm: SelectorMap) -> bool:
    """⟨PAY⟩ done: a confirmation number is present post-act (execution §3.2).

    PAY is the gate; "done" is judged after the consented click by CONFIRM-style
    re-perception — a confirmation/success marker in the fresh tree."""
    return _any_name_contains(sm, _CONFIRMATION_MARKERS)


def confirm_done(state: ClarionState, sm: SelectorMap) -> bool:
    """CONFIRM done: success marker + confirmation # grounded (execution §3.2).

    Requires BOTH a confirmation marker in the tree AND a speakable (grounded)
    fact recording it — the success readback the blind user can verify."""
    if not _any_name_contains(sm, _CONFIRMATION_MARKERS):
        return False
    sayable = speakable(state["grounded_facts"])
    return any(
        any(m in f.value.lower() for m in _CONFIRMATION_MARKERS) for f in sayable
    ) or _any_name_contains(sm, _CONFIRMATION_MARKERS)


# ---------------------------------------------------------------------------
# Negative checks (execution §3.2 "Negative verification" column)
# A negative check returns True when the named bad state is ABSENT (page safe).
# ---------------------------------------------------------------------------


def no_error_banner(state: ClarionState, sm: SelectorMap) -> bool:
    """AUTH negative: "no error banner; no 'locked' state"."""
    for n in sm.nodes.values():
        if n.role in ("alert", "status") and any(
            m in n.name.lower() for m in _ERROR_MARKERS
        ):
            return False
    return not _any_name_contains(sm, ("locked",))


def no_autopay_scheduled(state: ClarionState, sm: SelectorMap) -> bool:
    """LOCATE negative: "no autopay already-scheduled state we'd duplicate"."""
    return not _any_name_contains(sm, _AUTOPAY_MARKERS)


def no_required_field_blank(state: ClarionState, sm: SelectorMap) -> bool:
    """FILL negative: "**no required field left blank**" (execution §3.2).

    The load-bearing negative: returns False the moment any required fillable
    field is empty — the exact condition ST1 accept #2 drives."""
    return all(_is_filled(n) for n in _required_fields(sm))


def no_silent_validation_error(state: ClarionState, sm: SelectorMap) -> bool:
    """FILL negative: "no silent validation error" — the one the screen reader
    never announced (execution §3.2)."""
    return not _has_validation_error(sm)


def no_surprise_fee(state: ClarionState, sm: SelectorMap) -> bool:
    """REVIEW negative: "no surprise fee/upsell added to total"."""
    return not _any_name_contains(sm, _FEE_UPSELL_MARKERS)


def confirmation_present(state: ClarionState, sm: SelectorMap) -> bool:
    """⟨PAY⟩ negative: "confirmation number present post-act"."""
    return _any_name_contains(sm, _CONFIRMATION_MARKERS)


def not_still_on_form(state: ClarionState, sm: SelectorMap) -> bool:
    """CONFIRM negative: "no error/timeout; not still on the form" — the
    silent-fail check (execution §3.2). If a submit/pay button is still present
    AND no confirmation appeared, we are silently still on the form."""
    if _has_validation_error(sm):
        return False
    still_on_form = any(
        n.role == "button"
        and any(w in n.name.lower() for w in ("pay", "submit", "confirm"))
        for n in sm.nodes.values()
    )
    if still_on_form and not _any_name_contains(sm, _CONFIRMATION_MARKERS):
        return False
    return True


# ---------------------------------------------------------------------------
# The registries — names → callables (the seam a model planner drops into later)
# ---------------------------------------------------------------------------

DONE_PREDICATES: dict[str, Predicate] = {
    "auth_done": auth_done,
    "locate_done": locate_done,
    "fill_done": fill_done,
    "review_done": review_done,
    "pay_done": pay_done,
    "confirm_done": confirm_done,
}

NEGATIVE_CHECKS: dict[str, Predicate] = {
    "no_error_banner": no_error_banner,
    "no_autopay_scheduled": no_autopay_scheduled,
    "no_required_field_blank": no_required_field_blank,
    "no_silent_validation_error": no_silent_validation_error,
    "no_surprise_fee": no_surprise_fee,
    "confirmation_present": confirmation_present,
    "not_still_on_form": not_still_on_form,
}


def resolve_done_predicate(name: str) -> Predicate:
    """Resolve a registered done-predicate by name. Raises if unknown — a stage
    must never carry a name that resolves to nothing (silent always-pass)."""
    try:
        return DONE_PREDICATES[name]
    except KeyError as e:
        raise KeyError(
            f"unknown done_predicate {name!r}; registered: {sorted(DONE_PREDICATES)}"
        ) from e


def resolve_negative_check(name: str) -> Predicate:
    try:
        return NEGATIVE_CHECKS[name]
    except KeyError as e:
        raise KeyError(
            f"unknown negative_check {name!r}; registered: {sorted(NEGATIVE_CHECKS)}"
        ) from e


def stage_advances(
    state: ClarionState,
    sm: SelectorMap,
    done_predicate: str,
    negative_checks: list[str],
) -> bool:
    """A stage advances iff its done-predicate holds AND every negative check
    holds (the named bad state is absent). The single machine gate CONFIRM calls;
    never the model's say-so (execution §3.3)."""
    if not resolve_done_predicate(done_predicate)(state, sm):
        return False
    return all(resolve_negative_check(nc)(state, sm) for nc in negative_checks)


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
    is the RESCUE trigger: the stage graph branches to the rescue sub-flow, then
    returns to the interrupted stage (execution §3 note)."""
    return [n for n in sm.nodes.values() if is_choked_widget(n)]


def needs_rescue(sm: SelectorMap) -> bool:
    """True iff any widget in the current tree chokes the screen reader."""
    return any(is_choked_widget(n) for n in sm.nodes.values())


__all__ = [
    "Predicate",
    "DONE_PREDICATES",
    "NEGATIVE_CHECKS",
    "resolve_done_predicate",
    "resolve_negative_check",
    "stage_advances",
    "is_choked_widget",
    "detect_rescue",
    "needs_rescue",
    # individual checks (importable for tests / a model planner)
    "auth_done",
    "locate_done",
    "fill_done",
    "review_done",
    "pay_done",
    "confirm_done",
    "no_error_banner",
    "no_autopay_scheduled",
    "no_required_field_blank",
    "no_silent_validation_error",
    "no_surprise_fee",
    "confirmation_present",
    "not_still_on_form",
]
