"""Acceptance spike — the REAL ``GeminiReasoner`` on a REAL usa.gov page
(architecture migration Step 2, "spike Gemini structured output with a per-call
enum of 50+ live ids" — the Next-research note).

Marked ``live`` (network + Playwright + real Chromium + a real Gemini key), so it
is EXCLUDED from the deterministic gate. Run:

    pytest clarion -m live -k gemini_reasoner_spike -s

Distinct from ``test_reasoner_spike_live.py`` (which proves the CONTRACT shapes
via the ``FakeReasoner``): THIS proves the real adapter — it grounds the live map
+ facts, calls the REAL ``GeminiReasoner.decide_step`` with the FULL live map as a
per-call enum, asserts the returned ``target_index`` resolves in the live map, the
``value_ref`` resolves to a real ``Fact.id`` (or a justified null), ``say`` is a
verbatim grounded substring, and ``validate_step_proposal`` PASSES it. Then it
proves the FENCE: a forced off-page index is REJECTED by the guard. It prints the
decode latency (the <800ms turn-budget signal) + whether the schema was honored
with the ~48-id map. No site-specific code.
"""

from __future__ import annotations

import pytest

from clarion.contracts.state import SelectorMap, StepProposal
from clarion.kernel.reasoner_guard import resolve_value_ref, validate_step_proposal

pytestmark = pytest.mark.live

USA_GOV_URL = "https://www.usa.gov/benefits"


@pytest.mark.asyncio
async def test_gemini_reasoner_spike_on_real_usa_gov_page() -> None:
    from dotenv import load_dotenv

    from clarion.actuator.actuator import PlaywrightActuator
    from clarion.adapters.gemini_reasoner import GeminiReasoner
    from clarion.app.page_retriever import PageRetriever

    load_dotenv()  # real GOOGLE_API_KEY, per the BEHAVIORAL-on-a-real-site rule

    actuator = await PlaywrightActuator.create(USA_GOV_URL, headless=True)
    try:
        # 1. Perceive the LIVE numbered map + read LIVE grounded facts.
        live_map: SelectorMap = await actuator.perceive()
        retriever = PageRetriever(actuator)
        facts = await retriever.query("find benefits I may qualify for", k=12)

        assert live_map.nodes, "expected a non-empty live SelectorMap"
        assert facts, "expected grounded facts off the real page"

        # 2. The FULL live map IS the ranked_slice (the per-call enum of ~all live
        #    ids — the schema-honoring stress the Next-research note asks for).
        reasoner = GeminiReasoner()
        goal = "find benefits I may qualify for on this page"
        proposal = await reasoner.decide_step(goal, live_map, facts, history=[])
        decide_ms = reasoner.last_decide_ms

        # 3. The proposal resolves to a REAL live node + a REAL Fact.id (or a
        #    justified null), and the guard PASSES it (the real fence verdict).
        good = validate_step_proposal(proposal, live_map, facts)
        assert good.ok, good.reason
        assert proposal.target_index in live_map.nodes, (
            f"target_index {proposal.target_index} not in the live map"
        )
        target_node = live_map.nodes[proposal.target_index]
        resolved = resolve_value_ref(proposal.value_ref, facts)
        if proposal.value_ref is not None:
            assert resolved is not None and resolved.id == proposal.value_ref
            assert resolved.source_node_id  # grounded to a real AX nodeId
        # 'say', when non-empty, MUST be a verbatim substring of a grounded span.
        if proposal.say:
            assert any(proposal.say in f.value for f in facts), (
                f"say {proposal.say!r} is not a verbatim grounded substring"
            )

        # 4. Prove the FENCE on the REAL adapter+guard: a forced off-page index is
        #    REJECTED (the structured-output-is-not-a-logit-mask catch).
        off_index = max(live_map.nodes) + 10_000
        bad_index = validate_step_proposal(
            StepProposal(action_kind="click", target_index=off_index),
            live_map,
            facts,
        )
        assert bad_index.ok is False
        bad_ref = validate_step_proposal(
            StepProposal(
                action_kind="fill",
                target_index=proposal.target_index,
                value_ref="fact-DANGLING-not-on-page",
            ),
            live_map,
            facts,
        )
        assert bad_ref.ok is False

        # 5. Print the live data exercised — proof it ran on real usa.gov + Gemini.
        print("\n=== GeminiReasoner spike — REAL usa.gov/benefits + REAL Gemini ===")
        print(f"model                 : {reasoner.model}")
        print(
            f"live map nodes        : {len(live_map.nodes)} "
            f"(per-call target_index enum size = {len(live_map.nodes) + 1})"
        )
        print(f"grounded facts        : {len(facts)} (value_ref enum size = {len(facts) + 1})")
        print(f"decide_ms (decode/TTFT): {decide_ms:.0f} ms")
        print(f"decide_calls          : {reasoner.decide_calls} (1 = schema honored, no re-ask)")
        print(f"scratch_reasoning     : {proposal.scratch_reasoning!r}")
        print(f"action_kind           : {proposal.action_kind}")
        print(f"validated target_index: {proposal.target_index}")
        print(
            f"  -> live node        : role={target_node.role!r} "
            f"name={target_node.name!r} node_id={target_node.node_id!r}"
        )
        print(f"validated value_ref   : {proposal.value_ref}")
        if resolved is not None:
            print(f"  -> Fact.value       : {resolved.value!r}")
            print(f"  -> Fact.source_node_id: {resolved.source_node_id!r}")
        print(f"irreversibility       : {proposal.irreversibility} "
              f"({proposal.irreversibility_rationale!r})")
        print(f"success_check         : {proposal.success_check!r}")
        print(f"say (verbatim)        : {proposal.say!r}")
        print(f"REJECTED off-page index {off_index}: ok={bad_index.ok} "
              f"reason={bad_index.reason}")
        print(f"REJECTED dangling value_ref: ok={bad_ref.ok} reason={bad_ref.reason}")

        # The headline latency assertion: the decode happened in some finite time;
        # we report it (the <800ms budget is a turn-level overlap concern, not a
        # hard gate on the cold decode, which the SpeculationController hides).
        assert decide_ms is not None and decide_ms > 0
    finally:
        await actuator.close()
