"""K1 — the two-clause policy (foundation §1, mechanized).

This module is the kernel's conscience. It encodes the single invariant the whole
product rests on, split into its two clauses:

  1. **Epistemic clause** (``assert_grounded``): a ``Fact`` may NOT be marked
     ``verified`` (and therefore may NOT be spoken) unless it carries a
     ``source_node_id``. An ungrounded claim is silently dropped, never promoted.
     This is the "read what the screen reader reads, cite the source node"
     guarantee (execution §2.2 VERIFY / §4.1b).

  2. **Agentic clause** (``assert_consented``): an *irreversible* action may NOT
     be executed unless an ``approve`` decision for its proposal exists in the
     ``consent_log``. No irreversible ACT without an approved consent — the
     foundation §5 hard-stop, the agentic half of the invariant.

Both functions are PURE: no I/O, no provider SDKs, no langgraph. They take state
fragments and return decisions, so the graph nodes (and tests) can call them
directly. Keeping the policy out of the graph means it is independently testable
and cannot be silently bypassed by a node that "forgot" to check.
"""

from __future__ import annotations

from clarion.contracts.state import Consent, Fact, PairedFact, Proposal

__all__ = [
    "assert_grounded",
    "is_grounded",
    "is_member",
    "is_speakable_value",
    "pairing_backs",
    "is_consented",
    "assert_consented",
    "speakable",
    "PolicyViolation",
]


class PolicyViolation(Exception):
    """Raised when a hard policy guarantee would be violated. The kernel never
    catches this to "try anyway" — a violation is a bug, surfaced loudly."""


# ---------------------------------------------------------------------------
# Epistemic clause — grounding
# ---------------------------------------------------------------------------


def is_grounded(fact: Fact) -> bool:
    """A fact is grounded iff it carries a ``source_node_id`` (an AXTree node or
    retriever doc ref). Ungrounded facts may never be spoken (Fact docstring;
    execution §2.2)."""
    return fact.source_node_id is not None


def assert_grounded(facts: list[Fact]) -> list[Fact]:
    """The epistemic clause, mechanized.

    Returns a NEW list in which every grounded fact is marked ``verified=True``
    and every ungrounded fact is marked ``verified=False`` — regardless of what
    the caller set. An ungrounded fact (``source_node_id is None``) can therefore
    never come out of VERIFY marked verified, even if a model tried to assert it.

    This is deliberately total (no exception): VERIFY's job is to *refuse to
    verify* the ungrounded, not to crash the loop. The caller decides whether to
    drop the unverified facts before speaking; the kernel's PROPOSE/say path only
    ever reads ``verified`` facts.
    """
    out: list[Fact] = []
    for f in facts:
        grounded = is_grounded(f)
        # model_copy keeps the contract model immutable-by-convention: we never
        # mutate the caller's Fact in place.
        out.append(f.model_copy(update={"verified": grounded}))
    return out


def speakable(facts: list[Fact]) -> list[Fact]:
    """The facts the agent is allowed to say out loud: grounded AND verified."""
    return [f for f in facts if is_grounded(f) and f.verified]


# ---------------------------------------------------------------------------
# VERIFY set-membership + pairing fence (architecture invariant fences #2/#3)
#
# Upgrades the hollow ``source_node_id != None`` check: a value is speakable only
# if it is byte-identical to a Fact CURRENTLY in ``grounded_facts`` (membership,
# fence #2) AND — for an "X is Y" claim — a single ``PairedFact`` backs both
# halves from the SAME perceive cycle (pairing-correctness, fence #3). AG-PAIR's
# warning: AX nodeIds renumber across loads, so a stale pairing must not back a
# claim on a freshly-perceived page — callers pass the live ``paired_facts``.
#
# Pure: no I/O, no SDK. The kernel calls these AT VERIFY before forming the say.
# ---------------------------------------------------------------------------


def is_member(value: str, grounded_facts: list[Fact]) -> bool:
    """Fence #2 (set-membership). True iff ``value`` is byte-identical to the
    value of a Fact CURRENTLY in ``grounded_facts`` — replacing the hollow
    ``source_node_id != None`` check. A restated / paraphrased / fabricated span
    is not a member, so it can never be spoken. (Membership over the LIVE set, not
    a stale snapshot: a value read off the page last cycle but gone this cycle is
    no longer a member.)"""
    return any(f.value == value for f in grounded_facts)


def is_speakable_value(value: str, grounded_facts: list[Fact]) -> bool:
    """A scalar value is speakable iff it is a member of the live grounded set
    (fence #2) AND that member is itself ``speakable`` (grounded + verified —
    fences #1/#4). The single membership gate the kernel calls before speaking a
    standalone value (no pairing claim)."""
    return any(
        f.value == value and is_grounded(f) and f.verified for f in grounded_facts
    )


def pairing_backs(
    label_text: str, value_text: str, paired_facts: list[PairedFact]
) -> bool:
    """Fence #3 (pairing-correctness). True iff a SINGLE ``PairedFact`` from the
    current perceive cycle backs both halves of an "X is Y" claim byte-identically
    (``PairedFact.backs``). Two separate true facts that no single geometric
    pairing joins return False — the mis-pairing (the past-due row's ``$142.10``
    read as the amount due) is ungroundable and refused. The caller passes the
    facts harvested THIS cycle so a renumbered/stale pairing cannot back a claim
    on a fresh page (AG-PAIR's nodeId-renumber warning)."""
    return any(p.backs(label_text, value_text) for p in paired_facts)


# ---------------------------------------------------------------------------
# Negative-claim routing (drives the NegativeVerifier honest-decline, fence #5)
#
# Which spoken lines need the coverage-aware closed-world check in
# ``kernel.negative_verifier``? The ones that ASSERT A NEGATIVE ("there is no late
# fee", "you have no autopay"). That polarity is decided by the LLM Reasoner and
# carried on ``StepProposal.asserts_absence`` (the model does the metacognition) —
# NOT by a lexical keyword/stopword list, which the de-hardcoding thesis bans. A
# positive read-back of a grounded value is already fenced by membership (#2), so
# it never needs the verifier. (The retired lexical ``is_negative_claim`` lived
# here; PROPOSE now routes on ``step.asserts_absence``.)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Agentic clause — consent before an irreversible act
# ---------------------------------------------------------------------------


def is_consented(proposal: Proposal, consent_log: list[Consent]) -> bool:
    """True iff an ``approve`` decision for ``proposal.id`` exists in the log.

    This is also the §2.3 idempotency substrate: ACT reads the same log to decide
    whether it is allowed to side-effect at all.
    """
    return any(
        c.proposal_id == proposal.id and c.decision == "approve"
        for c in consent_log
    )


def assert_consented(proposal: Proposal, consent_log: list[Consent]) -> bool:
    """The agentic clause, mechanized.

    Returns True if the action is *permitted to execute*:
      - a reversible action (``proposal.irreversible is False``) is always
        permitted — Fast mode auto-proceeds on these and Normal already gated it;
      - an irreversible action is permitted ONLY if an approved consent exists.

    Raises ``PolicyViolation`` if an irreversible action is attempted without an
    approved consent — that is the hard-stop the product refuses to cross. (ACT
    calls this *after* its own once-flag check, so a legitimate approved act
    passes and a bug — irreversible act with no yes — is surfaced, not silently
    executed.)
    """
    if not proposal.irreversible:
        return True
    if is_consented(proposal, consent_log):
        return True
    raise PolicyViolation(
        f"irreversible action for proposal {proposal.id!r} attempted without an "
        f"approved consent in the consent_log (agentic clause, foundation §5)"
    )
