"""The REAL voice→node resolve: does the live decide_step (MiniMax-M3) pick the
EXACT date×site cell from the full grid when the user names a date + site?

This is the de-hardcoded resolver (the LLM decides the index — no lexical match,
no ranker pre-prune). Run:
  cd agent && .venv/bin/python -m probes.recreation_decide_resolve_probe
"""
from __future__ import annotations

import asyncio

from clarion.actuator.actuator import PlaywrightActuator
from clarion.adapters.minimax_reasoner import MinimaxReasoner
from clarion.contracts.state import DecideContext

CAMP = "https://www.recreation.gov/camping/campgrounds/233359"

# (spoken intent, substring identifying the ONE correct cell)
CASES = [
    ("Book Site Boat A on June 12", "jun 12, 2026 - site boat a"),
    ("reserve Boat B for June 9", "jun 9, 2026 - site boat b"),
    ("the 16th, Boat A please", "jun 16, 2026 - site boat a"),
    ("I want site 003 on June 10", "jun 10, 2026 - site 003"),
]


async def main() -> None:
    reasoner = MinimaxReasoner()
    act = await PlaywrightActuator.create(CAMP, headless=True)
    try:
        await act._page.wait_for_timeout(4000)
        page = await act.perceive()
        readout = await act.describe_page()
        print(f"grid: {len(page.nodes)} nodes\n")

        for intent, needle in CASES:
            ctx = DecideContext(
                user_intent=intent,
                subgoal_index=0,
                subgoal_total=1,
                subgoal_description=f"Select the campsite cell for: {intent}",
                subgoal_done_check="node_added",
                plan=[f"Select the campsite cell for: {intent}"],
                page_title=readout.title,
                page_url=readout.url,
                page_summary=readout.summary,
                last_outcome="",
                recall_hint="",
            )
            step = await reasoner.decide_step(intent, page, [], [], context=ctx)
            idx = step.target_index
            chosen = page.nodes[idx].name if (idx is not None and idx in page.nodes) else "(none)"
            ok = needle in chosen.lower()
            print(f"[{'OK ' if ok else 'XX '}] intent={intent!r}")
            print(f"      kind={step.action_kind} target_index={idx} → {chosen!r}")
            print(f"      correct: {ok}\n")
    finally:
        await act.close()


if __name__ == "__main__":
    asyncio.run(main())
