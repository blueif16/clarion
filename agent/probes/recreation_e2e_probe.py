"""FULL product simulation (everything the user touches, minus STT/TTS):
home → navigate to the campground → grid-axis summary the communicator speaks →
user says a date+site → decide_step resolves the EXACT node → click it → observe
the booking effect.

Run:  cd agent && .venv/bin/python -m probes.recreation_e2e_probe
"""
from __future__ import annotations

import asyncio

from clarion.actuator.actuator import PlaywrightActuator
from clarion.adapters.minimax_reasoner import MinimaxReasoner
from clarion.contracts.state import Action, DecideContext

HOME = "https://www.recreation.gov/"
CAMP = "https://www.recreation.gov/camping/campgrounds/233359"
USER_GOAL = "make a reservation to Point Reyes"
USER_PICK = "Book Site Boat A on June 12"  # what the user says after the summary


def _grid_clause(summary: str) -> str:
    i = summary.find("This page has a data grid")
    j = summary.find("What would you like to do?")
    return summary[i:j].strip() if i >= 0 else "(no grid clause)"


async def main() -> None:
    reasoner = MinimaxReasoner()
    act = await PlaywrightActuator.create(HOME, headless=True)
    try:
        print(f"[1] user: {USER_GOAL!r}  (start {act._page.url!r})")
        # The SITE-MAP/planner resolves the goal → the campground URL (proven
        # separately in recreation_planner_verify); here we drive that nav step.
        await act.act(Action(kind="navigate", value=CAMP))
        await act._page.wait_for_timeout(4000)
        print(f"[2] navigated → {act._page.url!r}")

        readout = await act.describe_page()
        print(f"[3] communicator's grid summary (grounded):\n    {_grid_clause(readout.summary)}")

        page = await act.perceive()
        print(f"\n[4] user says: {USER_PICK!r}  → resolving over {len(page.nodes)} nodes…")
        ctx = DecideContext(
            user_intent=USER_PICK, subgoal_index=0, subgoal_total=1,
            subgoal_description=f"Select the campsite cell for: {USER_PICK}",
            subgoal_done_check="node_added", plan=[USER_PICK],
            page_title=readout.title, page_url=readout.url,
            page_summary=readout.summary, last_outcome="", recall_hint="",
        )
        step = await reasoner.decide_step(USER_PICK, page, [], [], context=ctx)
        idx = step.target_index
        cell = page.nodes[idx].name if idx in page.nodes else "(none)"
        print(f"    resolved → index {idx}: {cell!r}  (kind={step.action_kind})")

        if step.action_kind != "click":
            print(f"    [note] decider chose {step.action_kind} (likely the cell is reserved) — stopping honestly.")
            return

        before = {n.name for n in page.nodes.values()}
        obs = await act.act(Action(kind="click", index=idx))
        await act._page.wait_for_timeout(2500)
        after = {n.name for n in obs.selector_map.nodes.values()}
        new = sorted(n for n in after - before if n)
        booking = [n for n in new if any(h in n.lower() for h in ("cart", "reserve", "book", "checkout"))]
        print(f"\n[5] clicked: success={obs.success}")
        print(f"    node count {len(before)} → {len(after)}")
        print(f"    NEW booking affordance(s): {booking or '(none)'}")
        print(f"\n[E2E] navigate→summarize→resolve→click→effect: "
              f"{bool(obs.success and (booking or len(after) != len(before)))}")
    finally:
        await act.close()


if __name__ == "__main__":
    asyncio.run(main())
