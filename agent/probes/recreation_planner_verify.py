"""Live verification of the SITE-MAP reframe: does the real Reasoner navigate to
the right campground when the destination IS indexed, and fall back to SEARCH
when it is NOT — instead of confidently navigating to a wrong page?

Drives the real path: PlaywrightActuator.describe_page (homepage, with its real
search affordance) → SiteKnowledge.context_facts → _with_site_map → plan_goal
on the live MinimaxReasoner.

Run:  cd agent && .venv/bin/python -m probes.recreation_planner_verify
Needs .[spike] (MiniMax) + .[retrieval] (Moss). Assumes a few campgrounds are
already indexed (probes.recreation_multidoc_calib ingests them).
"""
from __future__ import annotations

import asyncio

from clarion.actuator.actuator import PlaywrightActuator
from clarion.adapters.minimax_reasoner import MinimaxReasoner
from clarion.app.site_indexer import SiteKnowledge
from clarion.stages.graph import _with_site_map
from clarion.stages.planner import plan_goal, verbalize_subgoals

HOME = "https://www.recreation.gov/"
GOALS = [
    ("PRESENT (indexed)", "I want to make a reservation to Point Reyes"),
    ("ABSENT (not indexed)", "I want to reserve a campsite at Zion National Park"),
]


async def main() -> None:
    reasoner = MinimaxReasoner()
    sk = SiteKnowledge()

    act = await PlaywrightActuator.create(HOME, headless=True)
    try:
        await act._page.wait_for_timeout(4000)
        orient = await act.describe_page()
    finally:
        await act.close()

    aff_names = [f.value for f in orient.affordances]
    has_search = any(
        "search" in (n or "").lower() for n in aff_names
    )
    print(f"homepage url={orient.url!r}")
    print(f"affordances: {len(orient.affordances)}  (search control present: {has_search})\n")

    for label, goal in GOALS:
        site_facts = await sk.context_facts(orient.url, goal)
        urls = []
        for f in site_facts:
            u = next((p[5:] for p in f.value.splitlines() if p.startswith("URL:")), "?")
            urls.append(u.rstrip("/").split("/")[-1])
        plan_orient = _with_site_map(orient, site_facts) if site_facts else orient
        subgoals = await plan_goal(reasoner, goal, plan_orient, list(orient.affordances))

        print(f"==== {label} ====")
        print(f"goal: {goal!r}")
        print(f"site-map candidates (top {len(urls)}): {urls}")
        print(f"PLAN: {verbalize_subgoals(subgoals)}")
        for i, sg in enumerate(subgoals):
            print(f"   {i}. {sg.description}   [done_check: {sg.done_check!r}]")
        plan_txt = " ".join(sg.description.lower() for sg in subgoals)
        print(f"   → mentions 'search': {'search' in plan_txt}")
        print(f"   → mentions 'point reyes': {'point reyes' in plan_txt or 'reyes' in plan_txt}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
