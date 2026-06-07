"""AG-GATE — the NegativeVerifier closed-world honest-decline (fence #5).

Deterministic, network-free unit tests for ``kernel.negative_verifier``. The
killer acceptance (architecture migration Step 5): a charge rendered as an
IMAGE/canvas (invisible to the AXTree → no fact at all) must HEDGE, not become a
confident "no late fee."

  (1) no asserting node + a grounded ``absent`` coverage fact → ASSERT (sourced);
  (2) no asserting node + NO coverage (image/canvas case) → HEDGE (the acceptance);
  (3) an asserting ``present`` fact exists → HEDGE the false negative (never speak);
  (4) caller-supplied ``region_covered`` permits the negative when nothing asserts;
  (5) the polarity that ROUTES a line into the verifier is the model's own
      ``StepProposal.asserts_absence`` self-report (the de-hardcoding that replaced
      the lexical ``is_negative_claim`` keyword list) — the end-to-end routing is
      proven in ``test_gate_wiring`` (covered → spoken, uncovered → hedged).

Pure: contracts + the kernel verifier; ZERO provider SDK.
"""

from __future__ import annotations

from clarion.contracts.state import Fact, StepProposal
from clarion.kernel.negative_verifier import (
    asserting_fact,
    covering_absent_fact,
    verify_negative,
)


def _present(value: str, node: str = "n-1") -> Fact:
    return Fact(value=value, source_node_id=node, polarity="present", verified=True)


def _absent(value: str, node: str = "n-2") -> Fact:
    return Fact(value=value, source_node_id=node, polarity="absent", verified=True)


# ---------------------------------------------------------------------------
# (1) covered negative → ASSERT (and it's sourced — the invariant holds)
# ---------------------------------------------------------------------------


def test_covered_negative_asserts_and_is_sourced() -> None:
    facts = [
        _present("Amount due: $84.32", "n-amt"),
        _absent("No late fee", "n-fee"),  # we READ "no late fee" off the page
    ]
    v = verify_negative("no late fee", facts)
    assert v.speak is True
    assert v.verdict == "assert"
    # Sourced from the grounded absent-fact node (no fact without a source).
    assert v.source_node_id == "n-fee"
    assert covering_absent_fact("no late fee", facts) is not None


# ---------------------------------------------------------------------------
# (2) THE KILLER ACCEPTANCE: image/canvas → no fact at all → HEDGE
# ---------------------------------------------------------------------------


def test_uncovered_negative_hedges_image_rendered_charge() -> None:
    """A late-fee charge rendered as an IMAGE is invisible to the AXTree → there is
    NO fact about it, present OR absent. A naive closed world would read the silence
    as 'no late fee'; the verifier HEDGES instead (the killer acceptance)."""
    # Only an unrelated grounded fact; nothing about a late fee (it's in an image).
    facts = [_present("Amount due: $84.32", "n-amt")]
    v = verify_negative("no late fee", facts)
    assert v.speak is False
    assert v.verdict == "hedge"
    assert asserting_fact("late fee", facts) is None  # not asserted…
    assert covering_absent_fact("late fee", facts) is None  # …and not covered


def test_empty_grounded_set_hedges() -> None:
    v = verify_negative("no autopay", [])
    assert v.verdict == "hedge"


# ---------------------------------------------------------------------------
# (3) an asserting present fact → HEDGE the false negative (never speak it)
# ---------------------------------------------------------------------------


def test_present_asserting_fact_blocks_a_false_negative() -> None:
    """If a late-fee fact IS on the page, 'no late fee' is simply false — the
    verifier refuses it (hedges) rather than speaking a contradiction."""
    facts = [_present("Late fee: $25.00", "n-fee")]
    assert asserting_fact("late fee", facts) is not None
    v = verify_negative("no late fee", facts)
    assert v.speak is False
    assert v.verdict == "hedge"
    assert "asserts" in v.reason


# ---------------------------------------------------------------------------
# (4) caller-supplied coverage permits the negative
# ---------------------------------------------------------------------------


def test_region_covered_flag_permits_negative_when_nothing_asserts() -> None:
    facts = [_present("Amount due: $84.32", "n-amt")]
    # No coverage fact, but the caller proved coverage by another route.
    covered = verify_negative("no late fee", facts, region_covered=True)
    assert covered.speak is True
    uncovered = verify_negative("no late fee", facts, region_covered=False)
    assert uncovered.speak is False
    # …but coverage NEVER overrides an asserting fact (the negative is still false).
    asserting = [_present("Late fee: $25.00", "n-fee")]
    assert verify_negative("no late fee", asserting, region_covered=True).speak is False


# ---------------------------------------------------------------------------
# (5) the routing signal is the model's self-report, not a lexical keyword list
# ---------------------------------------------------------------------------


def test_asserts_absence_is_the_self_reported_routing_signal() -> None:
    """A proposal carries the model's OWN polarity metacognition on
    ``asserts_absence`` (default False = a positive read-back; True routes the say
    through the verifier). This replaced the banned lexical ``is_negative_claim``
    keyword table — the end-to-end hedge/assert routing is proven in
    ``test_gate_wiring``."""
    positive = StepProposal(action_kind="read", say="Amount due: $84.32")
    assert positive.asserts_absence is False  # default: not a negative claim

    negative = StepProposal(action_kind="read", say="no late fee", asserts_absence=True)
    assert negative.asserts_absence is True
