"""PRODUCT-FAITHFUL end-to-end: does clicking a date×site availability CELL on the
Point Reyes grid actually DO something through our actuator?

Simulates the user path with the REAL actuator (same cdp_click_by_backend the
extension product runs): home → navigate to the campground → perceive the grid →
click ONE 'is available' cell → re-perceive → report the OBSERVED effect (url,
node-count delta, the cell's own state change, and any reserve/cart/booking
affordance that appears). Honest: it reports whatever actually happens.

Run:  cd agent && .venv/bin/python -m probes.recreation_cell_click_probe
"""
from __future__ import annotations

import asyncio

from clarion.actuator.actuator import PlaywrightActuator
from clarion.contracts.state import Action

HOME = "https://www.recreation.gov/"
CAMP = "https://www.recreation.gov/camping/campgrounds/233359"
# Evidence that a click had a downstream effect (probe-side observation only — not
# product logic): a reservation/selection affordance the grid did NOT offer before.
EFFECT_HINTS = ("reserve", "add to cart", "book", "checkout", "selected",
                "night", "remove", "cart", "continue")


def _snapshot(sm):
    return {i: (n.role, n.name, tuple(sorted(k for k, v in n.state.items() if v)))
            for i, n in sm.nodes.items()}


async def main() -> None:
    act = await PlaywrightActuator.create(HOME, headless=True)
    try:
        # 1. Navigate home → campground (the agent's resolved nav step).
        print(f"[1] start: {act._page.url!r}")
        await act.act(Action(kind="navigate", value=CAMP))
        await act._page.wait_for_timeout(4000)  # SPA grid settle
        print(f"[2] navigated → {act._page.url!r}")

        # 2. Perceive the grid; pick a concrete 'is available' cell (what the user
        #    names: a specific date×site). Prefer a 'BOAT A' available cell.
        before_sm = await act.perceive()
        avail = [(i, n) for i, n in sorted(before_sm.nodes.items())
                 if n.role == "button" and "is available" in n.name.lower()]
        if not avail:
            print("!! no 'is available' cell found — grid may not have rendered.")
            return
        target_i, target_n = next(
            ((i, n) for i, n in avail if "boat a" in n.name.lower()), avail[0]
        )
        print(f"[3] grid perceived: {len(before_sm.nodes)} nodes, "
              f"{len(avail)} available cells")
        print(f"    TARGET cell [{target_i}] {target_n.name!r}")

        url_before = act._page.url
        before = _snapshot(before_sm)

        # 3. CLICK the cell via the real actuator (same path the product runs).
        obs = await act.act(Action(kind="click", index=target_i))
        await act._page.wait_for_timeout(2500)  # let selection/panel settle
        after_sm = obs.selector_map
        after = _snapshot(after_sm)
        url_after = act._page.url

        # 4. Report the OBSERVED effect.
        print(f"\n[4] click dispatched: success={obs.success}  detail={obs.detail!r}")
        print(f"    url: {url_before!r} → {url_after!r}  (changed: {url_before != url_after})")
        print(f"    node count: {len(before)} → {len(after)}  (Δ {len(after) - len(before)})")

        # the clicked cell's own state, before vs after (by name match — index may shift)
        after_same = next((s for s in after.values() if s[1] == target_n.name), None)
        print(f"    TARGET cell state: before={before.get(target_i, ('?','?',()))[2]} "
              f"after={after_same[2] if after_same else '(cell gone/renamed)'}")

        # new node NAMES that look like a reservation effect
        before_names = {s[1].lower() for s in before.values()}
        new_effecty = sorted({
            s[1] for s in after.values()
            if s[1] and s[1].lower() not in before_names
            and any(h in s[1].lower() for h in EFFECT_HINTS)
        })
        print(f"    NEW reservation-effect affordances after click ({len(new_effecty)}):")
        for nm in new_effecty[:12]:
            print(f"        + {nm[:90]}")
        if not new_effecty:
            # fall back: show ANY new nodes so we can see what changed
            any_new = sorted({s[1] for s in after.values()
                              if s[1] and s[1].lower() not in before_names})
            print(f"    (no obvious reservation affordance; {len(any_new)} new nodes total)")
            for nm in any_new[:12]:
                print(f"        · {nm[:90]}")

        verdict = (url_before != url_after) or (len(after) != len(before)) or \
                  (after_same and after_same[2] != before.get(target_i, ('','',()))[2]) or \
                  bool(new_effecty)
        print(f"\n[VERDICT] the data cell is CLICKABLE and had an observable effect: {bool(verdict)}")
    finally:
        await act.close()


if __name__ == "__main__":
    asyncio.run(main())
