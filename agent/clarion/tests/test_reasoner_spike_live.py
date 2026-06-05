"""Migration Step-2 acceptance spike — REAL usa.gov page (architecture migration
Step 2: "a 20-line spike on a real page's map+facts — decide_step returns an
existing index, a resolvable value_ref, and rejects an off-page index").

Marked ``live`` (network + Playwright + a real Chromium), so it is EXCLUDED from
the deterministic gate (run: ``pytest clarion -m live -k reasoner_spike -s``). It
proves the Wave-A contract shapes are sound on REAL data, with no site-specific
code: it grounds the live map + facts, calls the ``FakeReasoner``, and shows the
``reasoner_guard`` rejects an off-page index + a dangling value_ref.
"""

from __future__ import annotations

import pytest

from clarion.contracts.state import SelectorMap, StepProposal
from clarion.fakes import FakeReasoner
from clarion.kernel.reasoner_guard import resolve_value_ref, validate_step_proposal

pytestmark = pytest.mark.live

USA_GOV_URL = "https://www.usa.gov/benefits"


@pytest.mark.asyncio
async def test_reasoner_spike_on_real_usa_gov_page() -> None:
    from dotenv import load_dotenv

    from clarion.actuator.actuator import PlaywrightActuator
    from clarion.app.page_retriever import PageRetriever

    load_dotenv()  # real keys, per the BEHAVIORAL-on-a-real-site validation rule

    actuator = await PlaywrightActuator.create(USA_GOV_URL, headless=True)
    try:
        # 1. Perceive the LIVE numbered map + read LIVE grounded facts.
        live_map: SelectorMap = await actuator.perceive()
        retriever = PageRetriever(actuator)
        facts = await retriever.query("benefits", k=8)

        assert live_map.nodes, "expected a non-empty live SelectorMap"
        assert facts, "expected grounded facts off the real page"

        # 2. Build a ranked_slice (top-K of the live map, SAME indices) + decide.
        top_k = dict(sorted(live_map.nodes.items())[:8])
        ranked_slice = SelectorMap(nodes=top_k, token_estimate=live_map.token_estimate)
        reasoner = FakeReasoner()
        proposal = await reasoner.decide_step("benefits", ranked_slice, facts, history=[])

        # 3. The proposal resolves to a REAL live node + a REAL Fact.id.
        good = validate_step_proposal(proposal, live_map, facts)
        assert good.ok, good.reason
        target_node = live_map.nodes[proposal.target_index]
        resolved = resolve_value_ref(proposal.value_ref, facts)
        assert resolved is not None and resolved.id == proposal.value_ref
        assert resolved.source_node_id  # the value is grounded to a real AX nodeId

        # 4. Feed the guard an OFF-PAGE index and a DANGLING value_ref — REJECT both.
        off_index = max(live_map.nodes) + 10_000
        bad_index = validate_step_proposal(
            StepProposal(action_kind="click", target_index=off_index),
            live_map,
            facts,
        )
        bad_ref = validate_step_proposal(
            StepProposal(action_kind="fill", target_index=proposal.target_index,
                         value_ref="fact-DANGLING-not-on-page"),
            live_map,
            facts,
        )
        assert bad_index.ok is False
        assert bad_ref.ok is False

        # 5. Print the actual live data exercised (proof it ran on real usa.gov).
        print("\n=== Reasoner spike — REAL usa.gov/benefits ===")
        print(f"live map nodes: {len(live_map.nodes)} | grounded facts: {len(facts)}")
        print(f"validated target_index : {proposal.target_index}")
        print(f"  -> live node          : role={target_node.role!r} "
              f"name={target_node.name!r} node_id={target_node.node_id!r}")
        print(f"validated value_ref     : {proposal.value_ref}")
        print(f"  -> Fact.value         : {resolved.value!r}")
        print(f"  -> Fact.source_node_id: {resolved.source_node_id!r}")
        print(f"REJECTED off-page index {off_index}: ok={bad_index.ok} "
              f"reason={bad_index.reason}")
        print(f"REJECTED dangling value_ref: ok={bad_ref.ok} reason={bad_ref.reason}")
    finally:
        await actuator.close()
