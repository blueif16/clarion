"""AG-GATE — the dual-signal IrreversibilityGate structural net (killer-closer #2).

Deterministic, network-free unit tests for ``kernel.irreversibility``:

  (1) the STRUCTURAL net can ESCALATE a model ``reversible`` → ``unknown``
      (a consequential click with no grounded undo affordance → UNKNOWN-on-no-undo);
  (2) the model can NEVER DOWNGRADE the structural net (model ``reversible`` +
      structural ``unknown`` → ``unknown``; model ``irreversible`` is never relaxed);
  (3) the structural net is STRUCTURE, not a name keyword list (a button literally
      named "Pay" with a Cancel escape hatch on the page is NOT auto-escalated;
      a NAMELESS consequential control IS — it's unidentifiable);
  (4) a pure read / a fill into a textbox carries no structural escalation;
  (5) an opaque state flag (haspopup) escalates.

Pure: contracts + the kernel classifier; ZERO provider SDK.
"""

from __future__ import annotations

from clarion.contracts.state import Action, AxNode, Proposal, SelectorMap
from clarion.kernel.irreversibility import _structural_prescreen, classify


def _proposal(kind: str, index: int | None) -> Proposal:
    return Proposal(
        id="p1",
        utterance="…",
        action=Action(kind=kind, index=index),  # type: ignore[arg-type]
        irreversible=False,
    )


def _page(*nodes: AxNode) -> SelectorMap:
    return SelectorMap(nodes={n.index: n for n in nodes}, token_estimate=10)


# A consequential control with NO grounded undo/cancel anywhere on the page.
_NO_UNDO_PAGE = _page(
    AxNode(index=0, role="button", name="Continue", node_id="n-cont"),
)

# The SAME consequential control, but the page now offers a Cancel escape hatch —
# a structural reversibility witness, so the net stays neutral.
_HAS_UNDO_PAGE = _page(
    AxNode(index=0, role="button", name="Continue", node_id="n-cont"),
    AxNode(index=1, role="button", name="Cancel", node_id="n-cancel"),
)


# ---------------------------------------------------------------------------
# (1) the structural net ESCALATES reversible → unknown (UNKNOWN-on-no-undo)
# ---------------------------------------------------------------------------


def test_structural_net_escalates_consequential_click_with_no_undo() -> None:
    proposal = _proposal("click", 0)
    # The model thinks this benignly-named "Continue" is reversible…
    out = classify(proposal, _NO_UNDO_PAGE, "reversible")
    # …but with no grounded undo affordance, structure can't prove it → unknown.
    assert out == "unknown"
    # The pre-screen itself returns the escalation target.
    assert _structural_prescreen(proposal, _NO_UNDO_PAGE) == "unknown"


def test_structural_net_neutral_when_a_grounded_undo_exists() -> None:
    proposal = _proposal("click", 0)
    # A named consequential control on a page WITH a Cancel escape hatch: the net
    # has no opinion → the model's reversible judgement stands.
    assert _structural_prescreen(proposal, _HAS_UNDO_PAGE) is None
    assert classify(proposal, _HAS_UNDO_PAGE, "reversible") == "reversible"


# ---------------------------------------------------------------------------
# (2) the model can NEVER downgrade the structural net
# ---------------------------------------------------------------------------


def test_model_reversible_cannot_downgrade_structural_escalation() -> None:
    proposal = _proposal("click", 0)
    # Model says reversible; structure says unknown → the MORE-gating wins.
    assert classify(proposal, _NO_UNDO_PAGE, "reversible") == "unknown"


def test_model_irreversible_is_never_relaxed_by_a_neutral_net() -> None:
    proposal = _proposal("click", 0)
    # Structure is neutral (undo present) but the model said irreversible → stays.
    assert classify(proposal, _HAS_UNDO_PAGE, "irreversible") == "irreversible"


def test_garbage_model_judgment_fails_closed_to_unknown() -> None:
    proposal = _proposal("click", 0)
    # An empty/unrecognised model judgement is treated as unknown (gates), and the
    # structural net cannot relax it.
    assert classify(proposal, _HAS_UNDO_PAGE, "") == "unknown"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# (3) STRUCTURE, not a name keyword list (the deleted pay/submit matcher)
# ---------------------------------------------------------------------------


def test_no_name_keyword_list_pay_button_is_not_auto_escalated() -> None:
    """A button literally named 'Pay' on a page WITH an undo path is NOT escalated
    by the structural net — there is no pay/submit/confirm/send keyword list. (The
    MODEL may still judge it irreversible; that's a separate signal.)"""
    pay_page = _page(
        AxNode(index=0, role="button", name="Pay $84.32", node_id="n-pay"),
        AxNode(index=1, role="button", name="Go back", node_id="n-back"),
    )
    proposal = _proposal("click", 0)
    assert _structural_prescreen(proposal, pay_page) is None
    # Structure neutral → the model's call stands (reversible here).
    assert classify(proposal, pay_page, "reversible") == "reversible"


def test_nameless_consequential_control_escalates() -> None:
    """A consequential control with an EMPTY accessible name is unidentifiable —
    we cannot tell the user what they'd press → escalate (even WITH an undo path)."""
    nameless_page = _page(
        AxNode(index=0, role="button", name="", node_id="n-x"),
        AxNode(index=1, role="button", name="Cancel", node_id="n-cancel"),
    )
    proposal = _proposal("click", 0)
    assert _structural_prescreen(proposal, nameless_page) == "unknown"
    assert classify(proposal, nameless_page, "reversible") == "unknown"


# ---------------------------------------------------------------------------
# (4) non-consequential actions carry no structural escalation
# ---------------------------------------------------------------------------


def test_read_action_has_no_structural_opinion() -> None:
    proposal = _proposal("read", 0)
    assert _structural_prescreen(proposal, _NO_UNDO_PAGE) is None
    assert classify(proposal, _NO_UNDO_PAGE, "reversible") == "reversible"


def test_read_back_is_reversible_even_when_model_judged_irreversible() -> None:
    """A read performs no mutation → reversible by construction, regardless of the
    model judgement. PROPOSE degrades an off-page step to a side-effect-free read
    but carries the model's irreversibility judgement of the ABANDONED step; the
    classifier must not let that stale judgement flag the read irreversible (else
    consent_gate auto-proceeds the read to ACT and assert_consented hard-stops a
    harmless grounded read — the 'prop-0-0 irreversible without consent' crash)."""
    proposal = _proposal("read", None)
    assert classify(proposal, _NO_UNDO_PAGE, "irreversible") == "reversible"
    assert classify(proposal, _NO_UNDO_PAGE, "unknown") == "reversible"
    # And the degenerate empty/garbage judgement (would fail-closed to unknown for a
    # consequential control) is also harmless for a read.
    assert classify(proposal, _NO_UNDO_PAGE, "") == "reversible"  # type: ignore[arg-type]


def test_fill_into_textbox_has_no_structural_opinion() -> None:
    textbox_page = _page(
        AxNode(index=0, role="textbox", name="Amount", node_id="n-amt"),
    )
    proposal = _proposal("fill", 0)
    assert _structural_prescreen(proposal, textbox_page) is None
    assert classify(proposal, textbox_page, "reversible") == "reversible"


def test_bare_fill_capability_downgrades_model_unknown() -> None:
    """A bare fill (no submit) is reversible by OUR OWN capability — we can
    re-type or clear the field; the submit is the commit, not the typing. The
    model's conservative ``unknown`` hedge (no on-page undo) is relaxed, so a
    search-box fill no longer speaks "treat it as final" (the live 06-11 run)."""
    page = _page(
        AxNode(index=0, role="searchbox", name="Search Recreation.gov", node_id="n-s"),
    )
    proposal = _proposal("fill", 0)
    assert classify(proposal, page, "unknown") == "reversible"


def test_bare_fill_model_irreversible_is_never_relaxed() -> None:
    """Escalate-only intact: a model that CONFIDENTLY judges a fill irreversible
    (an auto-committing field it recognises) still gates — the capability axis
    only relaxes the hedged ``unknown``."""
    page = _page(AxNode(index=0, role="textbox", name="Amount", node_id="n-amt"))
    proposal = _proposal("fill", 0)
    assert classify(proposal, page, "irreversible") == "irreversible"


def _submit_fill_proposal(index: int) -> Proposal:
    return Proposal(
        id="p1",
        utterance="…",
        action=Action(kind="fill", index=index, value="point reyes", submit=True),
        irreversible=False,
    )


def test_submitting_fill_always_gates() -> None:
    """A fill with ``submit=True`` presses Enter — it COMMITS the query like a
    button press, so it is NOT capability-exempt and the structural net holds it
    at ``unknown`` (gates), even over a model ``reversible``. Fail-closed: we
    cannot structurally prove what the Enter commits."""
    page = _page(
        AxNode(index=0, role="searchbox", name="Search Recreation.gov", node_id="n-s"),
    )
    assert _structural_prescreen(_submit_fill_proposal(0), page) == "unknown"
    assert classify(_submit_fill_proposal(0), page, "unknown") == "unknown"
    assert classify(_submit_fill_proposal(0), page, "reversible") == "unknown"


# ---------------------------------------------------------------------------
# (5) an opaque state flag escalates
# ---------------------------------------------------------------------------


def test_opaque_state_flag_escalates() -> None:
    popup_page = _page(
        AxNode(
            index=0,
            role="button",
            name="Options",
            state={"haspopup": True},
            node_id="n-opt",
        ),
        AxNode(index=1, role="button", name="Cancel", node_id="n-cancel"),
    )
    proposal = _proposal("click", 0)
    assert _structural_prescreen(proposal, popup_page) == "unknown"
    assert classify(proposal, popup_page, "reversible") == "unknown"


def test_consequential_click_on_a_nonexistent_index_fails_closed() -> None:
    """A click whose target index isn't in the live map is structurally
    unidentifiable → unknown (fail-closed)."""
    proposal = _proposal("click", 99)
    assert _structural_prescreen(proposal, _HAS_UNDO_PAGE) == "unknown"
    assert classify(proposal, _HAS_UNDO_PAGE, "reversible") == "unknown"


# ---------------------------------------------------------------------------
# (6) CAPABILITY-AWARE reversibility — actions WE can put back are not over-gated
#     ("can WE revert this?"): a navigate (re-navigate to url_before) and a click
#     on a re-selectable control are reversible by OUR capability, so the
#     fail-closed no-on-page-undo net must not escalate them. Grounded on kind +
#     role; escalate-only is preserved (a model irreversible is never relaxed).
# ---------------------------------------------------------------------------


def test_navigate_is_not_overgated_when_no_on_page_undo() -> None:
    """A navigation is reversible by OUR capability (we re-navigate to the recorded
    prior URL), so even with no on-page Cancel the net has no opinion → the model's
    judgement stands."""
    proposal = _proposal("navigate", None)
    assert _structural_prescreen(proposal, _NO_UNDO_PAGE) is None
    assert classify(proposal, _NO_UNDO_PAGE, "reversible") == "reversible"


def test_navigate_model_irreversible_is_still_respected() -> None:
    """The capability exemption only withholds the structural ESCALATION — it never
    relaxes a model that judged the navigation irreversible (escalate-only intact)."""
    proposal = _proposal("navigate", None)
    assert classify(proposal, _NO_UNDO_PAGE, "irreversible") == "irreversible"


def test_reselectable_click_is_not_overgated() -> None:
    """A click on a re-selectable control (radio/checkbox/switch/tab/menuitem — a
    'multiple choice') is reversible by re-clicking, so it is exempt from the
    no-on-page-undo net even though the page has no Cancel affordance."""
    for role in ("radio", "checkbox", "switch", "tab", "menuitem"):
        page = _page(AxNode(index=0, role=role, name="Option A", node_id="n-a"))
        proposal = _proposal("click", 0)
        assert _structural_prescreen(proposal, page) is None, role
        assert classify(proposal, page, "reversible") == "reversible", role


def test_capability_exemption_does_not_leak_to_a_bare_button() -> None:
    """The exemption must NOT relax a bare button (which could submit/commit): a
    click on a role=button with no grounded undo STILL escalates to unknown — the
    capability axis only covers navigate + link + re-selectable roles."""
    proposal = _proposal("click", 0)
    assert _structural_prescreen(proposal, _NO_UNDO_PAGE) == "unknown"
    assert classify(proposal, _NO_UNDO_PAGE, "reversible") == "unknown"


def test_link_click_is_navigation_reversible() -> None:
    """A click on a LINK navigates; we revert by re-navigating to url_before, so it
    is reversible by capability even on a page with NO Cancel affordance. This is the
    common redirect path the reasoner actually uses (it clicks links, not navigate
    actions) — regression for the live usa.gov undo refusal (link-nav read as
    `unknown` → the spoken verdict + `undo_last` were both wrong)."""
    link_page = _page(
        AxNode(index=0, role="link", name="Help with energy bills", node_id="n-link")
    )
    proposal = _proposal("click", 0)
    assert _structural_prescreen(proposal, link_page) is None
    assert classify(proposal, link_page, "reversible") == "reversible"


def test_link_click_model_irreversible_is_still_respected() -> None:
    """Escalate-only intact: a link the MODEL judged irreversible (a commit link) is
    never relaxed to reversible by the exemption."""
    link_page = _page(
        AxNode(index=0, role="link", name="Confirm and pay", node_id="n-pay")
    )
    proposal = _proposal("click", 0)
    assert classify(proposal, link_page, "irreversible") == "irreversible"


# ---------------------------------------------------------------------------
# (6b) CAPABILITY DOWNGRADE of the conservative UNKNOWN — the live pay.gov fix.
#      Withholding the structural escalation (6) is NOT enough: the model itself
#      hedges ``unknown`` for a navigation (no on-screen undo to point at), and
#      ``_escalate("unknown", None)`` keeps it ``unknown`` → still gated, still
#      "I can't be sure I can undo this, so I'll treat it as final" on a plain
#      redirect. ``classify`` must DOWNGRADE a capability-reversible move from
#      ``unknown`` → ``reversible``. A confident ``irreversible`` is never touched;
#      a bare button is never downgraded.
# ---------------------------------------------------------------------------


def test_link_click_model_unknown_is_downgraded_to_reversible() -> None:
    """THE live-session regression: on pay.gov home the reasoner clicked the
    'Make a Loan Payment' LINK and judged it ``unknown`` (no on-page undo), so the
    gate said 'treat it as final' on a plain navigation. A link-click is reversible
    by capability → ``unknown`` is downgraded to ``reversible`` (no consent stop)."""
    link_page = _page(
        AxNode(index=0, role="link", name="Make a Loan Payment", node_id="n-loan")
    )
    proposal = _proposal("click", 0)
    assert classify(proposal, link_page, "unknown") == "reversible"


def test_navigate_model_unknown_is_downgraded_to_reversible() -> None:
    proposal = _proposal("navigate", None)
    assert classify(proposal, _NO_UNDO_PAGE, "unknown") == "reversible"


def test_reselectable_click_model_unknown_is_downgraded_to_reversible() -> None:
    for role in ("radio", "checkbox", "switch", "tab", "menuitem"):
        page = _page(AxNode(index=0, role=role, name="Option A", node_id="n-a"))
        assert classify(_proposal("click", 0), page, "unknown") == "reversible", role


def test_unknown_downgrade_does_not_leak_to_a_bare_button() -> None:
    """The downgrade is capability-scoped: a bare button judged ``unknown`` (it might
    submit/commit, and we cannot re-click to revert it) STAYS ``unknown`` → gated."""
    proposal = _proposal("click", 0)
    assert classify(proposal, _NO_UNDO_PAGE, "unknown") == "unknown"
