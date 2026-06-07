"""Gap 1 — the page-grounded GROUND source: ``extract_text_facts`` (pure harvest
over a real-shaped AX tree) + ``PageRetriever`` (goal-ranked, always grounded).

These pin the invariant-critical behavior: the kernel grounds on the REAL page,
every fact carries a live AX ``nodeId``, and a page lacking the value yields no
fact (honest absence) — never a fixture constant. Pure: feeds AX-tree dicts shaped
exactly like ``Accessibility.getFullAXTree`` (positive-id StaticText, negative-id
InlineTextBox leaves, ignored nodes), no provider SDK, no browser.
"""

from __future__ import annotations

from clarion.actuator.pipeline import extract_text_facts
from clarion.app.page_retriever import PageRetriever
from clarion.contracts.state import AxNode, Fact, SelectorMap


def _ax(node_id: str, role: str, name: str, *, ignored: bool = False) -> dict:
    """One ``Accessibility.getFullAXTree`` node dict (the shape extract_text_facts
    reads): nodeId / ignored / role.value / name.value."""
    return {
        "nodeId": node_id,
        "ignored": ignored,
        "role": {"value": role},
        "name": {"value": name},
    }


def _real_shaped_tree() -> dict:
    """An AX tree shaped like a real account page: value-bearing StaticText with
    REAL (positive) nodeIds, the synthetic NEGATIVE-id InlineTextBox leaves that
    duplicate them, an ignored node, headings, and an interactive control (which is
    an affordance, not text content)."""
    return {
        "nodes": [
            _ax("24", "RootWebArea", "Northwind Utilities"),
            _ax("55", "heading", "Account & Billing"),
            _ax("13", "StaticText", "Amount due"),
            _ax("14", "StaticText", "$84.32"),
            _ax("15", "StaticText", "Due date"),
            _ax("16", "StaticText", "June 15, 2026"),
            _ax("18", "StaticText", "NW-4417-0093"),
            # An InlineTextBox leaf: synthetic NEGATIVE id, duplicates a StaticText.
            _ax("-1000000041", "InlineTextBox", "$84.32"),
            # An ignored node must never surface.
            _ax("99", "StaticText", "hidden offscreen text", ignored=True),
            # An interactive control is an affordance, not text content → dropped.
            _ax("69", "button", "Pay bill"),
        ]
    }


# ---------------------------------------------------------------------------
# extract_text_facts — the pure harvest
# ---------------------------------------------------------------------------


def test_extract_grounds_real_text_and_skips_inline_ignored_interactive() -> None:
    facts = extract_text_facts(_real_shaped_tree())
    by_value = {f.value: f for f in facts}

    # The value-bearing StaticText is grounded to its REAL nodeId.
    assert by_value["$84.32"].source_node_id == "14"
    assert by_value["June 15, 2026"].source_node_id == "16"
    assert by_value["NW-4417-0093"].source_node_id == "18"
    # Headings (content) are kept and grounded too.
    assert "Account & Billing" in by_value

    # Every surfaced fact is grounded + verified (the epistemic clause can speak it)
    # and carries a REAL (positive) nodeId.
    for f in facts:
        assert f.source_node_id and not f.source_node_id.startswith("-")
        assert f.verified is True

    values = [f.value for f in facts]
    # The synthetic InlineTextBox duplicate of "$84.32" did NOT add a second entry.
    assert values.count("$84.32") == 1
    # Ignored text and the interactive control never appear.
    assert "hidden offscreen text" not in values
    assert "Pay bill" not in values


def test_extract_empty_when_no_text_content() -> None:
    tree = {
        "nodes": [
            _ax("1", "button", "Submit"),
            _ax("2", "textbox", "Email"),
            _ax("3", "StaticText", "secret", ignored=True),
        ]
    }
    assert extract_text_facts(tree) == []


# ---------------------------------------------------------------------------
# PageRetriever — grounded, goal-ranked, honest absence
# ---------------------------------------------------------------------------


class _FactsActuator:
    """Minimal actuator exposing ``read_facts`` (the full-AXTree path)."""

    def __init__(self, tree: dict) -> None:
        self._tree = tree

    async def read_facts(self) -> list[Fact]:
        return extract_text_facts(self._tree)


class _MapOnlyActuator:
    """An actuator WITHOUT ``read_facts`` (e.g. the offline cached transport) — the
    PageRetriever must fall back to the interactive map, still grounded."""

    def __init__(self, sm: SelectorMap) -> None:
        self._sm = sm

    async def perceive(self) -> SelectorMap:
        return self._sm


async def test_page_retriever_ranks_value_facts_for_the_goal() -> None:
    retriever = PageRetriever(_FactsActuator(_real_shaped_tree()))
    # k=5 is the kernel's GROUND default (kernel.graph.ground → query(goal)).
    facts = await retriever.query("Find the amount, payee, and due date", k=5)

    # Everything returned is grounded to a real page node (never ungrounded).
    assert facts and all(f.source_node_id and not f.source_node_id.startswith("-") for f in facts)
    # The retrieval timestamp was stamped (the latency-meter substrate).
    assert all(f.retrieved_at > 0 for f in facts)
    # The value-bearing lines the goal needs are surfaced — the actual values, each
    # read off the real page (not a fixture constant).
    top_values = {f.value for f in facts}
    assert {"$84.32", "June 15, 2026", "NW-4417-0093"} <= top_values
    # Ranking is real: the zero-relevance heading is pushed out of the top-k by the
    # goal-relevant + value-bearing facts (it is the lowest-scored fact).
    assert "Account & Billing" not in top_values


async def test_page_retriever_returns_nothing_on_a_page_without_text() -> None:
    """Honest absence: a page with no readable value yields NO fact — the kernel
    then grounds nothing and declines, never a fixture constant."""
    empty = _FactsActuator({"nodes": [_ax("1", "button", "Continue")]})
    assert await PageRetriever(empty).query("pay my electric bill") == []


async def test_page_retriever_falls_back_to_selector_map_grounded() -> None:
    sm = SelectorMap(
        nodes={
            0: AxNode(index=0, role="button", name="Pay bill", node_id="n-pay"),
            1: AxNode(index=1, role="link", name="My Account", node_id="n-acct"),
        }
    )
    facts = await PageRetriever(_MapOnlyActuator(sm)).query("pay the bill")
    # Grounded from the map's node_ids — no fabrication, still sourced.
    assert facts and all(f.source_node_id for f in facts)
    assert {f.value for f in facts} == {"Pay bill", "My Account"}


# ---------------------------------------------------------------------------
# INTEGRATED behavior — the hop the isolated tests missed: the kernel's
# GROUND(PageRetriever) → VERIFY → PROPOSE must surface the VALUE the task needs,
# not the LABEL that merely shares words with the goal (the validated inversion).
# ---------------------------------------------------------------------------


class _PageActuator:
    """An actuator carrying a fillable field (interactive map) AND page text
    (read_facts): the amount lives as StaticText '$84.32', its label as 'Amount
    due'. PageRetriever must rank the value first so PROPOSE fills with '$84.32'."""

    def __init__(self) -> None:
        from clarion.contracts.state import Action, Observation, PageDiff  # noqa: F401

        self._sm = SelectorMap(
            nodes={
                0: AxNode(index=0, role="textbox", name="Payment amount",
                          state={"required": True}, node_id="n-amt-field"),
                1: AxNode(index=1, role="button", name="Submit payment", node_id="n-submit"),
            }
        )

    async def perceive(self) -> SelectorMap:
        return self._sm

    async def read_facts(self) -> list[Fact]:
        return [
            Fact(value="Amount due", source_node_id="13", verified=True),
            Fact(value="$84.32", source_node_id="14", verified=True),
            Fact(value="Pay your bill", source_node_id="2", verified=True),
        ]

    async def act(self, action):  # noqa: ANN001
        from clarion.contracts.state import Observation
        return Observation(selector_map=self._sm, success=True)

    async def diff(self, before, after):  # noqa: ANN001
        from clarion.contracts.state import PageDiff
        return PageDiff()


class _FillTopRankedReasoner:
    """A deterministic Reasoner that fills the first interactive index with the
    TOP-RANKED grounded fact (facts[0] — PageRetriever has already ranked the
    value '$84.32' above its label). Stands in for GeminiReasoner; proves the
    kernel forms the grounded fill from whatever value the ranker floated up."""

    def __init__(self) -> None:
        self.last_decide_ms = None

    async def plan_goal(self, goal, orient, affordances):  # noqa: ANN001, ARG002
        from clarion.contracts.state import Subgoal
        return [Subgoal(description=goal, done_check="field_nonempty")]

    async def decide_step(self, goal, ranked_slice, facts, history, context=None):  # noqa: ANN001, ARG002
        from clarion.contracts.state import StepProposal
        target = next(iter(sorted(ranked_slice.nodes)), None)
        return StepProposal(
            scratch_reasoning="fill with the top-ranked value",
            action_kind="fill",
            target_index=target,
            value_ref=facts[0].id if facts else None,
            irreversibility="reversible",
            success_check="field_nonempty",
            say=facts[0].value if facts else "",
        )


async def test_propose_fills_with_the_value_not_the_label() -> None:
    """End-to-end through the kernel: GROUND grounds the page via PageRetriever,
    VERIFY marks it speakable, the Reasoner-driven PROPOSE forms the fill — and the
    value it fills / speaks is the real amount '$84.32' (the top-ranked grounded
    fact), NOT the label 'Amount due'. (The ranker floats the value up; the kernel
    fences the fill to the membership-verified verbatim span.)"""
    from clarion.kernel.graph import build_kernel, seed_state

    act = _PageActuator()
    kernel = build_kernel(_FillTopRankedReasoner(), PageRetriever(act), act, mode="fast")
    st = seed_state(goal="Find the payment amount due", mode="fast")
    st["page_index"] = await act.perceive()
    result = await kernel.ainvoke(st, {"configurable": {"thread_id": "prop-value"}})

    prop = result["pending_proposal"]
    assert prop is not None and prop.action is not None
    assert prop.action.kind == "fill"
    # The load-bearing assertion: the VALUE, not the label.
    assert prop.action.value == "$84.32", f"PROPOSE used {prop.action.value!r}, not the value"
    assert "$84.32" in prop.utterance and "Amount due" not in prop.utterance


# ---------------------------------------------------------------------------
# The ranker is a HINT, never the decider: query(k) is a top-K slice, but
# query_all is the UNFILTERED fallback so over-pruning the slice can never cause a
# false honest-decline on a value that is actually on the page (architecture PARSE).
# ---------------------------------------------------------------------------


def _many_facts_tree() -> dict:
    """A page with several value-bearing lines + a low-relevance one, so a small
    top-K provably PRUNES a real fact that the unfiltered fallback still returns."""
    return {
        "nodes": [
            _ax("13", "StaticText", "Amount due"),
            _ax("14", "StaticText", "$84.32"),
            _ax("15", "StaticText", "Due date"),
            _ax("16", "StaticText", "June 15, 2026"),
            _ax("17", "StaticText", "Confirmation number"),
            _ax("18", "StaticText", "NW-4417-0093"),
            _ax("19", "StaticText", "Late fee"),
            _ax("20", "StaticText", "$5.00"),
        ]
    }


async def test_query_returns_a_topk_hint_slice() -> None:
    retriever = PageRetriever(_FactsActuator(_many_facts_tree()))
    hint = await retriever.query("find the amount due", k=2)
    # The hint is exactly the top-K slice (a hint, not the whole set).
    assert len(hint) == 2
    assert all(f.source_node_id for f in hint)


async def test_query_all_unfiltered_fallback_returns_facts_the_hint_pruned() -> None:
    """The over-pruning guard: a small top-K hint DROPS some grounded facts, but
    ``query_all`` returns the FULL grounded set — so a later honest-decline re-checks
    everything before giving up (over-pruning can't cause a false give-up)."""
    retriever = PageRetriever(_FactsActuator(_many_facts_tree()))
    hint = await retriever.query("find the amount due", k=2)
    fallback = await retriever.query_all("find the amount due")

    hint_values = {f.value for f in hint}
    fallback_values = {f.value for f in fallback}

    # The fallback is a strict SUPERSET of the hint — nothing the hint had is lost.
    assert hint_values < fallback_values
    # And it contains real value-bearing facts the top-K hint pruned (e.g. the late
    # fee + the confirmation number) — these are reachable for a re-check.
    pruned = fallback_values - hint_values
    assert "$5.00" in pruned or "NW-4417-0093" in pruned
    # Every fallback fact is still grounded to a real node (never a fabrication).
    assert all(f.source_node_id and not f.source_node_id.startswith("-") for f in fallback)
    # The fallback also stamps the retrieval time (latency-meter substrate).
    assert all(f.retrieved_at > 0 for f in fallback)


async def test_query_all_honest_absence_on_empty_page() -> None:
    empty = _FactsActuator({"nodes": [_ax("1", "button", "Continue")]})
    assert await PageRetriever(empty).query_all("pay my bill") == []
