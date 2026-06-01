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

from clarion.contracts.state import Consent, Fact, Proposal

__all__ = [
    "assert_grounded",
    "is_grounded",
    "is_consented",
    "assert_consented",
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
