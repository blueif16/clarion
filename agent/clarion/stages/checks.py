"""The generic done-check evaluator — the SELECTED success check, evaluated in
CODE against the re-perceived page (architecture killer-closer #3).

> done is a code SELECTION: the Reasoner *selects* a registered generic check;
> CODE evaluates it against the freshly re-perceived tree (``diff_maps`` + a
> semantic anchor like a URL change or grounded status Fact). A step advances
> only on a real page-state check — never the model's say-so.

This file is the SEAM AG-KERNEL exposed (``evaluate_success_check`` is CALLED by
the generic executor in ``stages.graph``). AG-DONE hardens it: the five checks
are now generic, site-agnostic, and **settling-aware** (a benign SPA poll
re-render no longer false-positives ``navigated`` / ``node_added``), and they
certify against a real **semantic anchor** (the page URL before/after the act,
threaded by the executor) rather than a bare structural delta.

The five registered checks (the canonical set, shared with
``adapters.gemini_reasoner.SUCCESS_CHECKS`` and ``Subgoal.done_check``):
  - ``field_nonempty``    — a fillable target now carries a value (FILL worked).
  - ``node_added``        — the page grew a CONTENTFUL node (a result/confirmation
                            surfaced) — not a bare re-render churn artifact.
  - ``error_absent``      — no alert/status error + no invalid field flag.
  - ``navigated``         — the page genuinely moved off the prior view: the
                            semantic anchor (URL) changed, OR — absent a URL — a
                            SUBSTANTIAL structural delta (added/removed nodes, not
                            a one-node poll re-render).
  - ``confirmation_fact`` — a grounded confirmation/success Fact is speakable, OR
                            a confirmation marker surfaced in the re-perceived tree.

Pure: ``clarion.contracts`` + the pure ``pipeline.diff_maps`` + the policy
``speakable`` gate. ZERO provider SDK, ZERO langgraph.

The semantic anchor is a single string the executor threads through. AG-DONE's
convention (``make_anchor`` builds it, ``_split_anchor`` reads it): the URL
BEFORE and the URL AFTER the act, joined by ``\\x00`` — ``"<before>\\x00<after>"``.
A legacy single-string anchor (just the current URL, no separator) is treated as
"URL unknown / no before-after pair" and falls through to the structural signal,
so the check never regresses on an actuator that can't report a URL.
"""

from __future__ import annotations

from typing import Optional

from clarion.actuator.pipeline import diff_maps
from clarion.contracts.state import AxNode, ClarionState, SelectorMap
from clarion.kernel.policy import speakable

__all__ = ["evaluate_success_check", "SUCCESS_CHECKS", "make_anchor"]

# The canonical registered checks (a SELECTION the Reasoner picks by name). Kept
# in lock-step with ``adapters.gemini_reasoner.SUCCESS_CHECKS``.
SUCCESS_CHECKS: tuple[str, ...] = (
    "field_nonempty",
    "node_added",
    "error_absent",
    "navigated",
    "confirmation_fact",
)

# Fillable roles — what a FILL must populate (a value-bearing target).
_FILLABLE_ROLES = {"textbox", "searchbox", "combobox", "spinbutton", "textarea"}
# Roles a freshly-surfaced result/confirmation/status typically takes — a
# settling-aware ``node_added`` counts a CONTENTFUL or live-region add, never a
# bare re-render artifact. Generic (an ARIA live region on ANY site), no markers.
_RESULT_ROLES = {"alert", "status", "heading", "dialog", "alertdialog"}
# Confirmation / success markers — substring, case-insensitive. Generic English
# success vocabulary, NOT site-specific (no payee/amount/topology names).
_CONFIRMATION_MARKERS = ("confirmation", "confirmed", "success", "receipt", "complete")
# Error markers on an alert/status node — the surfaced-error signal.
_ERROR_MARKERS = ("error", "invalid", "incorrect", "locked", "try again", "failed")

# The before/after anchor separator (the executor joins ``before_url`` + this +
# ``after_url``). A NUL byte never appears in a real URL, so splitting is safe.
_ANCHOR_SEP = "\x00"


# ---------------------------------------------------------------------------
# The semantic anchor (AG-DONE): a before/after URL pair the executor threads.
# ---------------------------------------------------------------------------


def make_anchor(before_url: Optional[str], after_url: Optional[str]) -> Optional[str]:
    """Build the semantic anchor the executor threads into ``navigated``.

    The anchor encodes the page URL BEFORE the act and the URL AFTER it, so
    ``navigated`` certifies against a genuine page-state change rather than a
    bare structural delta (which a benign SPA re-render can also produce).

    Returns ``None`` when neither URL is known (the actuator can't report a URL —
    e.g. a fake/replay transport); ``navigated`` then falls through to the
    structural-delta signal. A NUL-joined ``"<before>\\x00<after>"`` string is
    the wire format ``_split_anchor`` reads.
    """
    if not before_url and not after_url:
        return None
    return f"{before_url or ''}{_ANCHOR_SEP}{after_url or ''}"


def _split_anchor(anchor: Optional[str]) -> Optional[tuple[str, str]]:
    """Read a before/after URL pair out of the threaded anchor, or ``None`` when
    the anchor carries no usable pair (absent, or a legacy single URL with no
    separator). A pair with two blank URLs is also ``None`` (no signal)."""
    if not anchor or _ANCHOR_SEP not in anchor:
        return None
    before, after = anchor.split(_ANCHOR_SEP, 1)
    if not before and not after:
        return None
    return before, after


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
        anchor:      the semantic anchor — a ``make_anchor(before_url, after_url)``
                     before/after URL pair threaded by the executor. ``navigated``
                     certifies against a real URL change when the pair is present;
                     otherwise it falls back to a substantial structural delta.

    Returns True iff the named check certifies the step's page-state effect. An
    UNKNOWN / empty check name fails CLOSED (returns False) — a step never
    advances on an unrecognised or unset check (no silent always-pass).

    Every check is GENERIC and site-agnostic (zero site-specific markers / no
    AUTH→…→CONFIRM topology). The checks read the diff (``diff_maps``) + the live
    re-perceived tree + the grounded facts + the semantic anchor, and are
    settling-aware so a benign SPA poll re-render does not false-positive
    ``navigated`` / ``node_added``.
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
# The five generic, settling-aware checks
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
    after_fillable = [n for n in after.nodes.values() if n.role in _FILLABLE_ROLES]
    if not after_fillable:
        return False
    before_by_id = {n.node_id: n for n in before.nodes.values()}
    for n in after_fillable:
        was = before_by_id.get(n.node_id)
        # newly-filled (blank→value): the field is now non-empty AND it either
        # did not exist before or was blank before. A field that was already
        # filled does NOT count (no-op step would otherwise pass).
        if _is_filled(n) and (was is None or not _is_filled(was)):
            return True
    return False


def _contentful_added(before: SelectorMap, after: SelectorMap) -> list[AxNode]:
    """The genuinely-meaningful added nodes (settling-aware): nodes present in
    ``after`` (by stable role+name+node_id identity) but not ``before``, that
    either carry a non-blank accessible name OR take a result/live-region role.

    This filters the bare re-render churn a benign SPA poll produces (a node
    re-keyed with an empty name, a layout shuffle) from a real result/confirmation
    /error surfacing. Generic — no site markers; ``_RESULT_ROLES`` is the ARIA
    live/landmark vocabulary that announces a change on ANY site."""
    diff = diff_maps(before, after)
    if not diff.added:
        return []
    added_idx = set(diff.added)
    out: list[AxNode] = []
    for n in after.nodes.values():
        if n.index not in added_idx:
            continue
        if n.name.strip() or n.role in _RESULT_ROLES:
            out.append(n)
    return out


def _node_added(before: SelectorMap, after: SelectorMap) -> bool:
    """The page grew a CONTENTFUL node — a result/confirmation/error/status
    surfaced (not a bare re-render churn artifact). Settling-aware: an added node
    with an empty name and a non-result role (a poll re-render re-keying a blank
    container) does NOT certify."""
    return bool(_contentful_added(before, after))


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


def _structural_fingerprint(sm: SelectorMap) -> frozenset[str]:
    """A stable, order-independent fingerprint of a tree's CONTENTFUL interactive/
    landmark surface (role+name+node_id of every named or result-role node). Used
    only as the URL-less navigation fallback — two identical fingerprints mean the
    page did not meaningfully move."""
    return frozenset(
        f"{n.role}\x00{n.name}\x00{n.node_id}"
        for n in sm.nodes.values()
        if n.name.strip() or n.role in _RESULT_ROLES
    )


def _navigated(
    before: SelectorMap, after: SelectorMap, anchor: Optional[str]
) -> bool:
    """The page genuinely moved off the prior view (the SEMANTIC ANCHOR).

    Primary signal — the URL: when the executor threads a before/after URL pair,
    ``navigated`` is True IFF the URL actually changed (a same-URL re-render is
    NOT a navigation, so an SPA poll can't false-positive).

    Fallback — no URL pair (an actuator that can't report a URL): a SUBSTANTIAL
    structural change. We require the CONTENTFUL surface to differ (the
    fingerprint moved) AND the diff to add or remove a node — a pure ``changed``
    churn (a bbox jitter / state toggle on the same nodes) is a settling artifact,
    not a navigation."""
    pair = _split_anchor(anchor)
    if pair is not None:
        before_url, after_url = pair
        # A real URL is known on both sides → the URL change IS the navigation
        # signal (definitive; immune to SPA re-render noise on a stable URL).
        if before_url and after_url:
            return before_url != after_url
        # Only one side known (e.g. the page navigated to a fresh URL we couldn't
        # read on the prior view) → fall through to the structural fallback.

    diff = diff_maps(before, after)
    if not (diff.added or diff.removed):
        return False  # pure churn / no delta → not a navigation.
    return _structural_fingerprint(before) != _structural_fingerprint(after)


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
