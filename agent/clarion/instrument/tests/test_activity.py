"""Tests for the ACTIVITY projection (the action-trace feed's single data source).

``instrument.publisher.activity_items`` folds the append-only ``trace`` +
``consent_log`` into one ``ActivityItem`` per decided action (grouped by
``proposal_id``), and ``format_history_say`` turns the last N into the grounded
voice readback the ``read_history`` tool speaks verbatim.

These guard the contract both Feature A (HUD toast feed) and Feature B (voice
recall) rely on:
  - decision-bearing grouping by proposal_id (control-flow nodes excluded);
  - status derivation across the full lifecycle (proposed → awaiting_yes →
    done / rejected / abstained);
  - persist-by-stakes (irreversible / awaiting persists; reversible does not);
  - ``details`` carries EVERY real recorded field, nothing fabricated;
  - last-N ordering + the honest empty-history readback;
  - the readback is grounded (no field appears that wasn't recorded) and
    copy-clean (no banned role words).
"""

from __future__ import annotations

from clarion.contracts.state import Consent, TraceEvent
from clarion.instrument.publisher import (
    ActivityItem,
    activity_items,
    format_history_say,
)


# ---------------------------------------------------------------------------
# Trace builders — mirror the real kernel emit sites (kernel/graph.py).
# ---------------------------------------------------------------------------


def _propose(pid: str, at: float, *, kind: str, target: str = "", say: str = "", **extra):
    data = {"proposal_id": pid, "action_kind": kind, "target_name": target, "say": say}
    data.update(extra)
    return TraceEvent(node="PROPOSE", event="exit", at=at, data=data)


def _gate(pid: str, at: float, *, cls: str, rationale: str = ""):
    return TraceEvent(
        node="GATE",
        event="exit",
        at=at,
        data={"proposal_id": pid, "classification": cls, "gates": cls != "reversible", "rationale": rationale},
    )


def _consent(pid: str, at: float, *, decision: str, irreversible: bool = True, utterance: str = ""):
    return TraceEvent(
        node="CONSENT",
        event="exit",
        at=at,
        data={"proposal_id": pid, "decision": decision, "irreversible": irreversible, "utterance": utterance},
    )


def _act(pid: str, at: float, *, success: bool = True):
    return TraceEvent(node="ACT", event="info", at=at, data={"acted_proposal_id": pid, "success": success})


# A full hero lifecycle: a reversible read, then an irreversible submit that is
# gated → approved → acted.
def _hero_trace():
    return [
        _propose("p0", 1.0, kind="read", target="balance", say="Amount due: $84.22", source="acct::balance", scratch="reasoned about the amount"),
        _gate("p0", 1.1, cls="reversible", rationale="a read performs no mutation"),
        _act("p0", 1.2),
        _propose("p2", 2.0, kind="click", target="Submit payment", scratch="this submits the payment"),
        _gate("p2", 2.1, cls="irreversible", rationale="this submits a payment and the page has no undo"),
        _consent("p2", 2.2, decision="approve", utterance="I'm about to use Submit payment. Say yes to continue."),
        _act("p2", 2.3),
    ]


# ---------------------------------------------------------------------------
# activity_items — grouping, status, persist, details
# ---------------------------------------------------------------------------


def test_groups_by_proposal_id_one_item_per_action():
    items = activity_items({"trace": _hero_trace(), "consent_log": []})
    assert [i.proposal_id for i in items] == ["p0", "p2"]
    assert all(isinstance(i, ActivityItem) for i in items)


def test_excludes_control_flow_nodes():
    # PLANNER / EXECUTOR / CONFIRM / GROUND are NOT decision-bearing — never items.
    trace = _hero_trace() + [
        TraceEvent(node="PLANNER", event="exit", at=0.5, data={"n_subgoals": 3}),
        TraceEvent(node="EXECUTOR", event="exit", at=3.0, data={"subgoal": 0, "done": True}),
        TraceEvent(node="CONFIRM", event="exit", at=3.1, data={"nodes": 12}),
    ]
    items = activity_items({"trace": trace, "consent_log": []})
    assert {i.proposal_id for i in items} == {"p0", "p2"}


def test_read_is_reversible_done_and_does_not_persist():
    read = activity_items({"trace": _hero_trace(), "consent_log": []})[0]
    assert read.kind == "read"
    assert read.target == "balance"
    assert read.value == "Amount due: $84.22"
    assert read.status == "done"
    assert read.irreversibility == "reversible"
    assert read.persist is False
    assert read.resolved is True


def test_irreversible_submit_approved_and_acted_is_done():
    pay = activity_items({"trace": _hero_trace(), "consent_log": [Consent(proposal_id="p2", decision="approve", at=2.2)]})[1]
    assert pay.kind == "click"
    assert pay.target == "Submit payment"
    assert pay.irreversibility == "irreversible"
    assert pay.decision == "approve"
    assert pay.status == "done"


def test_awaiting_yes_persists_before_consent():
    # Drop the CONSENT + ACT — the gated irreversible step is awaiting a yes.
    trace = _hero_trace()[:5]
    pay = activity_items({"trace": trace, "consent_log": []})[1]
    assert pay.status == "awaiting_yes"
    assert pay.persist is True
    assert pay.resolved is False


def test_rejected_decision_status():
    trace = _hero_trace()[:5] + [_consent("p2", 2.2, decision="reject")]
    pay = activity_items({"trace": trace, "consent_log": []})[1]
    assert pay.status == "rejected"
    assert pay.decision == "reject"


def test_abstain_when_proposal_rejected_by_guard():
    # The off-page / ungroundable proposal: PROPOSE.info rejected=… → abstained.
    trace = [
        TraceEvent(node="PROPOSE", event="info", at=1.0, data={"proposal_id": "p0", "rejected": "off-page target_index"}),
        TraceEvent(node="PROPOSE", event="exit", at=1.0, data={"proposal_id": "p0", "irreversible": False}),
    ]
    item = activity_items({"trace": trace, "consent_log": []})[0]
    assert item.status == "abstained"


def test_failed_act_status():
    trace = _hero_trace()[:6] + [_act("p2", 2.3, success=False)]
    pay = activity_items({"trace": trace, "consent_log": []})[1]
    assert pay.status == "failed"


def test_details_carries_every_real_field_and_nothing_invented():
    pay = activity_items({"trace": _hero_trace(), "consent_log": []})[1]
    # Every real field the kernel recorded is present in details…
    assert pay.details["classification"] == "irreversible"
    assert pay.details["rationale"] == "this submits a payment and the page has no undo"
    assert pay.details["scratch"] == "this submits the payment"
    assert pay.details["decision"] == "approve"
    assert pay.details["success"] is True
    # …and there is no key we didn't write into the trace.
    allowed = {
        "proposal_id", "action_kind", "target_name", "say", "classification",
        "gates", "rationale", "decision", "irreversible", "utterance",
        "acted_proposal_id", "success", "scratch",
    }
    assert set(pay.details).issubset(allowed), set(pay.details) - allowed


def test_consent_log_folds_in_when_trace_consent_missing():
    # Robustness: even if the CONSENT.exit trace was clipped, the consent_log
    # still supplies the decision.
    trace = _hero_trace()[:5] + [_act("p2", 2.3)]
    pay = activity_items({"trace": trace, "consent_log": [Consent(proposal_id="p2", decision="approve", value=None, at=2.2)]})[1]
    assert pay.decision == "approve"
    assert pay.status == "done"


def test_empty_state_yields_no_items():
    assert activity_items({}) == []
    assert activity_items({"trace": [], "consent_log": []}) == []


# ---------------------------------------------------------------------------
# format_history_say — grounded, last-N, ordered, empty, copy-clean
# ---------------------------------------------------------------------------

_BANNED = ("assi" + "stant", "hel" + "per", "assi" + "st")


def test_history_say_is_grounded_and_ordered():
    items = activity_items({"trace": _hero_trace(), "consent_log": []})
    say = format_history_say(items, 3)
    # Names the real grounded value + the real target, in order (read then submit).
    assert "Amount due: $84.22" in say
    assert "balance" in say
    assert "Submit payment" in say
    assert say.index("balance") < say.index("Submit payment")
    assert "Last:" in say


def test_history_say_last_n_only():
    items = activity_items({"trace": _hero_trace(), "consent_log": []})
    say = format_history_say(items, 1)
    # Only the most recent action — the read is not mentioned.
    assert "Submit payment" in say
    assert "Amount due: $84.22" not in say


def test_history_say_awaiting_orients_to_the_yes():
    items = activity_items({"trace": _hero_trace()[:5], "consent_log": []})
    say = format_history_say(items, 3)
    assert "Submit payment" in say
    assert "yes" in say.lower()


def test_history_say_empty_is_honest_not_fabricated():
    say = format_history_say([], 3)
    assert "haven't taken any steps" in say.lower()


def test_history_say_has_no_banned_role_words():
    items = activity_items({"trace": _hero_trace(), "consent_log": []})
    for n in (0, 1, 3, 10):
        low = format_history_say(items, n).lower()
        for bad in _BANNED:
            assert bad not in low, (bad, low)
    assert all(b not in format_history_say([], 3).lower() for b in _BANNED)
