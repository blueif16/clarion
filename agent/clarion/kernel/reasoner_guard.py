"""Code-side post-decode validation of a ``StepProposal`` (architecture
Components / GeminiReasoner: "Off-page indices/values caught by code-side
post-decode validation + reject").

Structured output is NOT a logit mask: the Gemini schema constrains SHAPE
(an int field, a string field) but the model can still emit an integer that is
not a live index, or a ``value_ref`` string that resolves to no real ``Fact``.
These pure functions are the fence — they run AFTER the decode, BEFORE the
proposal can reach the consent gate / ACT, and they fail closed.

A NEW file on purpose (it does not touch ``graph.py`` / ``policy.py``, so it
won't collide with the later AG-KERNEL rewire). Pure: contracts + stdlib only,
ZERO provider SDK.
"""

from __future__ import annotations

from dataclasses import dataclass

from clarion.contracts.state import Fact, SelectorMap, StepProposal


@dataclass(frozen=True)
class GuardResult:
    """The verdict of ``validate_step_proposal``. ``ok`` is the accept/reject flag;
    ``reason`` names WHY a rejection fired (audit + replan signal). A clean accept
    carries an empty reason."""

    ok: bool
    reason: str = ""


def _fact_ids(facts: list[Fact]) -> frozenset[str]:
    """The set of resolvable ``Fact.id`` the ``value_ref`` enum may point at."""
    return frozenset(f.id for f in facts)


def validate_step_proposal(
    proposal: StepProposal,
    live_map: SelectorMap,
    facts: list[Fact],
) -> GuardResult:
    """Reject a ``StepProposal`` that points off the live page. Fail-closed:

      1. ``target_index`` (when present) MUST be a key in the LIVE ``SelectorMap``.
         An index the model invented but the page never offered is rejected — it
         can never be clicked/filled. A ``None`` target_index is allowed only for a
         value-less action that needs no node (kept liberal here; the kernel's
         action-shape check is separate).
      2. ``value_ref`` MUST resolve to a real ``Fact.id`` in ``facts`` — OR be
         ``None`` (a click carries no value, and ``None`` is explicitly accepted).
         A dangling ref (a value the model fabricated, not read off a grounded
         Fact) is rejected: this is the enum-over-real-ids fence that stops an
         ungrounded value from being spoken/filled.

    Pure; does not mutate. The kernel calls this and, on ``ok is False``, discards
    the proposal and replans (never acts on it)."""
    # Fence 1 — target_index must be a live index.
    if proposal.target_index is not None:
        if proposal.target_index not in live_map.nodes:
            return GuardResult(
                ok=False,
                reason=(
                    f"target_index {proposal.target_index} is not in the live "
                    f"SelectorMap (live indices: "
                    f"{sorted(live_map.nodes)[:8]}{'…' if len(live_map.nodes) > 8 else ''})"
                ),
            )

    # Fence 2 — value_ref must resolve to a real Fact.id (null is fine).
    if proposal.value_ref is not None:
        if proposal.value_ref not in _fact_ids(facts):
            return GuardResult(
                ok=False,
                reason=(
                    f"value_ref {proposal.value_ref!r} resolves to no live Fact.id "
                    f"({len(facts)} grounded facts available)"
                ),
            )

    return GuardResult(ok=True)


def resolve_value_ref(value_ref: str | None, facts: list[Fact]) -> Fact | None:
    """Resolve a ``value_ref`` to the real grounded ``Fact`` it names (or ``None``
    if the ref is ``None`` or dangling). The kernel uses this AFTER a clean
    ``validate_step_proposal`` to get the verbatim value to fill/speak — so the
    spoken/filled value is always a byte-identical grounded span, never the
    model's restatement of it."""
    if value_ref is None:
        return None
    for fact in facts:
        if fact.id == value_ref:
            return fact
    return None


__all__ = ["GuardResult", "validate_step_proposal", "resolve_value_ref"]
