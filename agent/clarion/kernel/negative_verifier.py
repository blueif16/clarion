"""The NegativeVerifier — the coverage-aware honest-decline (architecture
Components / killer-closer #2 sibling, invariant fence #5).

> a spoken negative ("no late fee", "no autopay") comes ONLY from a closed-world
> search over ``grounded_facts`` finding no asserting node AND **coverage
> evidence** (we actually perceived the relevant region — distinguish "not
> afforded" from "couldn't perceive"); else it **downgrades to a hedge**.

The failure this closes (architecture migration Step 5 acceptance): a charge
rendered as an IMAGE/canvas is invisible to the AXTree, so a closed-world search
finds "no late-fee fact" — but that absence is an *artifact of not perceiving*,
not a real negative. A naive closed world would speak a confident "no late fee";
the killer acceptance is that we **HEDGE** instead.

So the verifier needs TWO signals, both fail-closed:
  1. **No asserting node** — no grounded ``Fact`` in the live set asserts the
     thing (a present-polarity fact whose value mentions the claim topic). If one
     exists, the negative is simply FALSE → never speak it.
  2. **Coverage evidence** — we actually perceived the region the claim is about.
     Coverage comes from either (a) a grounded ``Fact`` with ``polarity=="absent"``
     that the retriever/extractor asserted for this topic (an affirmative "there is
     no X here" read off the page — a first-class sourced negative, foundation §1),
     OR (b) a structural signal that the relevant region was perceived at all. Pure
     ``kernel`` cannot see canvas/image opacity (that needs an actuator AX
     enrichment — see the TODO), so coverage is established CONSERVATIVELY: only an
     explicitly-grounded ``absent`` Fact (or a caller-supplied coverage flag) proves
     coverage. Bare silence of the AXTree is NOT coverage → HEDGE.

The asymmetry is deliberate and fail-closed: it is always safe to hedge; it is
never safe to assert a false negative. When in doubt, hedge.

Pure: ``clarion.contracts`` + stdlib only. ZERO provider SDK (deterministic, ~ms).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from clarion.contracts.state import Fact

Verdict = Literal["assert", "hedge"]

__all__ = [
    "NegativeVerdict",
    "verify_negative",
    "topic_tokens",
    "asserting_fact",
    "covering_absent_fact",
]


# Tokens that don't help match a claim topic to a fact (drop them before overlap).
_STOPWORDS = frozenset(
    {
        "no", "not", "any", "there", "is", "are", "a", "an", "the", "of", "to",
        "for", "on", "in", "this", "that", "page", "here", "you", "your", "have",
        "has", "i", "we", "do", "does", "and", "or", "it",
    }
)


@dataclass(frozen=True)
class NegativeVerdict:
    """The outcome of a closed-world negative check.

    ``verdict``  : ``"assert"`` (the grounded negative may be spoken verbatim) or
                   ``"hedge"`` (downgrade to a non-committal line — we can't prove
                   the negative).
    ``reason``   : WHY (audit + the line the voice plane should hedge with when
                   ``verdict == "hedge"``).
    ``source_node_id`` : on an ``assert``, the grounded ``absent``-Fact node the
                   spoken negative is sourced from (so the invariant — no fact
                   without a source — still holds for the negative).
    """

    verdict: Verdict
    reason: str = ""
    source_node_id: Optional[str] = None

    @property
    def speak(self) -> bool:
        return self.verdict == "assert"


def _content_tokens(text: str) -> set[str]:
    """Punctuation-stripped lowercased tokens of ``text`` — the shared tokenizer
    so a topic ("late fee") and a fact value ("Late fee: $25.00") tokenize the
    SAME way ({"late", "fee", ...}); without this a trailing colon ("fee:") would
    silently break the overlap match."""
    return {t.strip(".,:;!?\"'()$%").lower() for t in text.split()}


def topic_tokens(claim_or_topic: str) -> frozenset[str]:
    """The content tokens of a claim/topic, lowercased, stopwords + the negation
    word ("no"/"not") dropped, so "no late fee" → {"late", "fee"}. Used to match a
    negative claim against the facts that would *assert its positive*."""
    toks = _content_tokens(claim_or_topic)
    return frozenset(t for t in toks if t and t not in _STOPWORDS and len(t) >= 2)


def asserting_fact(topic: str, grounded_facts: list[Fact]) -> Optional[Fact]:
    """Closed-world signal #1. Return a grounded ``present``-polarity Fact that
    ASSERTS the topic (its value mentions every content token of the topic), or
    ``None`` if none does.

    If such a fact exists the negative is simply FALSE (the thing IS present) —
    the caller must never speak "no late fee" when a late-fee fact is on the page.
    Matching is conservative (ALL topic tokens must appear) to avoid a spurious
    "asserts" on an unrelated fact; an over-narrow match only makes us hedge, which
    is the safe side."""
    want = topic_tokens(topic)
    if not want:
        return None
    for f in grounded_facts:
        if f.polarity != "present" or f.source_node_id is None:
            continue
        have = _content_tokens(f.value)
        if want <= have:
            return f
    return None


def covering_absent_fact(topic: str, grounded_facts: list[Fact]) -> Optional[Fact]:
    """Closed-world signal #2 (coverage). Return a grounded ``absent``-polarity
    Fact that affirmatively covers the topic — an "there is no X here" the
    retriever/extractor actually READ off the perceived region (a first-class
    sourced negative, foundation §1) — or ``None``.

    This is what distinguishes "not afforded" (we perceived the region and it has
    no late fee → a grounded ``absent`` Fact exists) from "couldn't perceive" (the
    region was an image/canvas → no fact at all, present OR absent → HEDGE). Bare
    silence is never coverage."""
    want = topic_tokens(topic)
    if not want:
        return None
    for f in grounded_facts:
        if f.polarity != "absent" or f.source_node_id is None:
            continue
        have = _content_tokens(f.value)
        # An absent-fact covers the topic if it shares any content token (it is an
        # explicit "no <topic>" reading); require at least one overlap so an
        # unrelated absent fact doesn't grant coverage.
        if want & have:
            return f
    return None


def verify_negative(
    topic: str,
    grounded_facts: list[Fact],
    *,
    region_covered: bool = False,
) -> NegativeVerdict:
    """The deterministic closed-world check behind the honest decline.

    A spoken negative about ``topic`` is permitted (``verdict == "assert"``) ONLY
    when BOTH hold:
      1. **No asserting node** — no grounded ``present`` Fact asserts the topic
         (else the negative is false; we must not say "no late fee" with a
         late-fee fact present).
      2. **Coverage evidence** — either a grounded ``absent`` Fact affirmatively
         read the topic off the perceived region, OR the caller passed
         ``region_covered=True`` (a future actuator coverage signal — see TODO).

    Otherwise → ``verdict == "hedge"`` (fail-closed). The two fences are
    asymmetric on purpose: an asserting fact is a HARD false-negative block; a
    missing coverage proof is the image/canvas case — the killer acceptance —
    where we hedge rather than guess.

    Pure + deterministic. ``region_covered`` defaults False so the SAFE outcome
    (hedge) is the default when no coverage is proven.
    """
    asserts = asserting_fact(topic, grounded_facts)
    if asserts is not None:
        # The thing IS present — a "no X" claim is simply false. Hedge (the caller
        # should actually speak the positive grounded fact instead, but the
        # verifier's job is only to refuse the false negative).
        return NegativeVerdict(
            verdict="hedge",
            reason=(
                f"a grounded fact asserts {topic!r} (source "
                f"{asserts.source_node_id}); the negative is false"
            ),
        )

    cover = covering_absent_fact(topic, grounded_facts)
    if cover is not None:
        # We affirmatively read "no <topic>" off the perceived region — speak it,
        # sourced (the invariant holds for the negative too).
        return NegativeVerdict(
            verdict="assert",
            reason=f"grounded absent-fact covers {topic!r}",
            source_node_id=cover.source_node_id,
        )

    if region_covered:
        # The caller proved coverage by another route (a future actuator signal)
        # and no asserting fact exists → an honest negative is permitted.
        return NegativeVerdict(
            verdict="assert",
            reason=f"region covered (caller-supplied) and nothing asserts {topic!r}",
        )

    # No asserting fact AND no coverage proof: this is the image/canvas dead-end.
    # The absence is an artifact of not perceiving, not a real negative → HEDGE.
    return NegativeVerdict(
        verdict="hedge",
        reason=(
            f"cannot prove a negative for {topic!r}: nothing asserts it but the "
            f"relevant region was not provably perceived (possible image/canvas)"
        ),
    )
