"""I1 — the page-grounded ``Retriever`` (kills the GROUND fixture).

This is the deep un-stub of Gap 1 (``docs/clarion-status.md``): the kernel's
GROUND used to query ``HeroRetriever``, a fixture of ``$84.32`` constants whose
``source_node_id``s (``"acct::balance"``) point at NO real node. On a real site
those constants are pure fabrication — there is nothing to coincidentally match.

``PageRetriever`` replaces it. ``query`` reads the **live page** through the
actuator (``read_facts`` → the shared ``extract_text_facts`` over the real AX
tree), so every returned ``Fact`` is text that is genuinely on the page, sourced
to a real AX ``nodeId``. The epistemic clause (``policy.assert_grounded``) then
lets the kernel speak it. A page that lacks the goal's values yields no matching
fact → the kernel grounds nothing and declines honestly, never a constant.

It is goal-conditioned at the retrieval layer: facts are ranked by overlap with
the query (the stage / user goal) plus a small bonus for value-bearing text
(currency, numbers, dates) — the lines a task actually needs. Pure ranking; the
only I/O is the actuator read.

This module OWNS only ``clarion/app/`` and imports the frozen ``Retriever`` port
+ contracts + the pure pipeline read-only. No provider SDK.
"""

from __future__ import annotations

import re
import time
from typing import Any

from clarion.actuator.pipeline import readout_from_selector_map
from clarion.contracts.ports import Retriever
from clarion.contracts.state import Fact

# A task usually needs to READ a VALUE ("$84.32"), not its LABEL ("Amount due") —
# and the label, not the value, is what shares words with a goal like "find the
# amount". So a pure token-overlap ranker inverts: the label outranks the value
# (validated bug — PROPOSE then fills a field with "Amount due"). We fix it by
# scoring value-bearing text ABOVE a bare label match, tiered by how value-like it
# is: a CURRENCY/decimal amount (the thing tasks most need) dominates; any other
# number (date, account/confirmation #) gets a smaller bump.
_CURRENCY_RE = re.compile(r"[\$£€]\s?\d|(?:^|\s)\d{1,3}(?:,\d{3})*\.\d{2}(?:\s|$)")
_NUMBER_RE = re.compile(r"\d")

# Stop-words stripped from the goal before token-overlap scoring (the stage goals
# read like "Find the amount, payee, and due date" — only the content words rank).
_STOP = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "to", "for", "on", "in", "at", "by",
        "with", "find", "all", "every", "before", "this", "that", "your", "my",
        "is", "are", "be", "it", "i", "we", "you", "page", "grounded",
    }
)


def _goal_tokens(goal: str) -> list[str]:
    """Content words of the goal, lower-cased, stop-words dropped (the ranking
    key). Keeps tokens of length >= 3 so 'due'/'pay' count but noise like 'a' does
    not."""
    words = re.findall(r"[a-z0-9]+", goal.lower())
    return [w for w in words if len(w) >= 3 and w not in _STOP]


def _score(fact: Fact, tokens: list[str]) -> tuple[int, int]:
    """Rank key for a page fact against the goal tokens. Higher is better.

    This is a **HINT, never the decider** (architecture PARSE / ContextRanker bullet):
    it ORDERS the grounded facts so the most goal-relevant value floats up, but
    ``query`` never lets it DROP a fact the Reasoner might need — the unfiltered set
    is always reachable via ``query_all`` so over-pruning can't cause a false decline.

    The pieces, tuned so a VALUE outranks its LABEL (the validated inversion):
      - ``overlap``: goal content-words appearing in the fact text;
      - ``value``: a CURRENCY/decimal amount scores high enough to beat a 2-word
        label match (4); any other number (date, account #) gets a small bump (1);
      - ``length_penalty``: a long paragraph that merely *contains* a goal word is
        demoted — a task wants the crisp value line, not the prose around it.
    The tie-break prefers the SHORTER fact (the dense value over a wordy match).
    """
    text = fact.value
    low = text.lower()
    overlap = sum(1 for t in tokens if t in low)
    if _CURRENCY_RE.search(text):
        value = 4
    elif _NUMBER_RE.search(text):
        value = 1
    else:
        value = 0
    length_penalty = -2 if len(text) > 120 else 0
    return (overlap + value + length_penalty, -len(text))


class PageRetriever(Retriever):
    """Grounds the kernel's GROUND in the REAL page (foundation §1). Wraps an
    actuator and, per ``query``, reads the live page's text facts and returns the
    ``k`` most goal-relevant — each sourced to a real AX ``nodeId``.

    The ranking is a **HINT, not a decider** (architecture PARSE / ContextRanker):
      - ``query(q, k)``   → the top-K ranked SLICE (the hint surfaced to the
        Reasoner); overrideable, never authoritative.
      - ``query_all(q)``  → the UNFILTERED fallback: every grounded fact, ranked but
        un-pruned, run before any honest-decline so over-pruning the slice can't
        cause a false give-up on a value that is actually on the page.

    Falls back to the interactive ``SelectorMap`` (``readout_from_selector_map``)
    for an actuator without ``read_facts`` (e.g. the offline ``CachedActuator``),
    so it degrades to grounded affordance text rather than ever fabricating a fact.
    Never returns an ungrounded fact: the VERIFY clause would refuse it anyway, and
    refusing here keeps the absence honest.
    """

    def __init__(self, actuator: Any) -> None:
        self._actuator = actuator
        # The queries seen (parity with HeroRetriever — handy for the panel/tests).
        self.calls: list[str] = []

    async def _page_facts(self) -> list[Fact]:
        """All grounded text facts on the current page (unranked). Prefers the
        full-AXTree ``read_facts``; falls back to the interactive map's names."""
        read_facts = getattr(self._actuator, "read_facts", None)
        if read_facts is not None:
            return await read_facts()
        # Fallback: ground from the numbered map (no body text, but still real).
        sm = await self._actuator.perceive()
        return list(readout_from_selector_map(sm).affordances)

    def _ranked(self, facts: list[Fact], q: str) -> list[Fact]:
        """All facts, RANKED by the goal hint but NOT pruned — stable order, every
        fact stamped with the retrieval time (the latency-meter substrate)."""
        tokens = _goal_tokens(q)
        ranked = sorted(facts, key=lambda f: _score(f, tokens), reverse=True)
        now = time.time()
        return [f.model_copy(update={"retrieved_at": now}) for f in ranked]

    async def query(self, q: str, *, k: int = 5) -> list[Fact]:
        """The frozen ``Retriever.query`` — returns the top-``k`` ranked slice as a
        HINT (the most goal-relevant grounded facts float up). This is a hint, NOT a
        verdict: it never originates a fact and never hides one the Reasoner needs —
        the full grounded set is always available via ``query_all`` so a later
        honest-decline can re-check before giving up (over-pruning ≠ a false decline).
        """
        self.calls.append(q)
        facts = await self._page_facts()
        if not facts:
            # Honest absence: nothing on the page to ground → the kernel speaks
            # nothing and (via the done-predicates) declines, never a constant.
            return []
        return self._ranked(facts, q)[:k]

    async def query_all(self, q: str = "") -> list[Fact]:
        """The UNFILTERED fallback (architecture PARSE bullet): EVERY grounded fact
        on the page, ranked by the same hint but with NO top-K cut. The kernel runs
        this BEFORE any honest-decline so an over-aggressive top-K can't make it give
        up on a value that is actually present — the decline only stands if the value
        is absent from the FULL set, not merely from the ranked slice. ``q`` only
        orders the result (the hint); passing ``""`` returns the facts unranked-by-
        relevance (still grounded, still all present)."""
        self.calls.append(q)
        facts = await self._page_facts()
        if not facts:
            return []
        return self._ranked(facts, q)


__all__ = ["PageRetriever", "extract_text_facts_ranked_score"]


# Exposed for tests that want to assert the ranking directly.
extract_text_facts_ranked_score = _score
